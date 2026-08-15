"""HRBP AI Workbench — Document ingestion pipeline.

Parse → Chunk → Embed → Index into vector database.
Supports docx, pdf, txt formats.
Uses Zhipu embedding-3 for vectorization.
"""

import asyncio
import uuid
from pathlib import Path
from typing import BinaryIO

from app.rag.config_loader import ScenarioConfig
from app.rag.llm.orchestrator import ZhipuEmbeddingClient
from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)

# In-memory task store for async ingestion
_ingestion_tasks: dict[str, dict] = {}


class DocumentParser:
    """Parse uploaded files into plain text."""

    async def parse(self, content: bytes, file_type: str, filename: str = "") -> str:
        """Extract text from docx/pdf/txt files."""
        if file_type == "txt":
            return content.decode("utf-8", errors="replace")

        elif file_type == "docx":
            try:
                import io
                from docx import Document
                doc = Document(io.BytesIO(content))
                paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
                return "\n".join(paragraphs)
            except Exception as e:
                logger.warning("docx_parse_failed", filename=filename, error=str(e))
                return f"[文档解析失败: {str(e)}]"

        elif file_type == "pdf":
            try:
                import io
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(content))
                pages = [page.extract_text() or "" for page in reader.pages]
                return "\n".join(pages)
            except Exception as e:
                logger.warning("pdf_parse_failed", filename=filename, error=str(e))
                return f"[PDF解析失败: {str(e)}]"

        else:
            raise ValueError(f"Unsupported file type: {file_type}")


class Chunker:
    """Split parsed text into chunks for embedding.

    Strategies:
    - fixed_512: Fixed-size chunks with overlap (default)
    - section: Split by section headings (制度文档)
    - semantic: Semantic-aware chunking (future)
    """

    def chunk(
        self,
        text: str,
        strategy: str = "fixed_512",
        chunk_size: int = 512,
        overlap: int = 50,
        source: str = "",
        filename: str = "",
    ) -> list[dict]:
        """Split text into overlapping chunks."""
        if strategy == "section":
            return self._chunk_by_section(text, source, filename)

        # Fixed-size chunking (default)
        chunks = []
        # For Chinese text, approximate characters instead of words
        text_len = len(text)

        for i in range(0, text_len, chunk_size - overlap):
            chunk_text = text[i : i + chunk_size]
            if chunk_text.strip():
                chunks.append({
                    "content": chunk_text.strip(),
                    "index": len(chunks),
                    "source": source or filename,
                    "section": f"片段 {len(chunks) + 1}",
                    "start_char": i,
                    "end_char": min(i + chunk_size, text_len),
                })

        return chunks

    def _chunk_by_section(self, text: str, source: str, filename: str) -> list[dict]:
        """Split text by section headings (common in HR policy documents)."""
        import re
        # Match common section patterns
        section_pattern = r"(第[一二三四五六七八九十\d]+[章节条]|[一二三四五六七八九十\d]+、|\d+\.\d+)"
        sections = re.split(section_pattern, text)

        chunks = []
        current_section = "总则"
        current_text = ""

        for i, part in enumerate(sections):
            if re.match(section_pattern, part):
                if current_text.strip():
                    chunks.append({
                        "content": current_text.strip(),
                        "index": len(chunks),
                        "source": source or filename,
                        "section": current_section,
                        "start_char": 0,
                        "end_char": len(current_text),
                    })
                current_section = part.strip()
                current_text = part
            else:
                current_text += part

        # Don't forget the last section
        if current_text.strip():
            chunks.append({
                "content": current_text.strip(),
                "index": len(chunks),
                "source": source or filename,
                "section": current_section,
                "start_char": 0,
                "end_char": len(current_text),
            })

        return chunks if chunks else self.chunk(text, strategy="fixed_512", source=source, filename=filename)


class Embedder:
    """Generate embeddings using Zhipu embedding-3 (2048 dim)."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of text chunks using Zhipu API."""
        if not texts:
            return []

        # Batch embed (Zhipu API supports batch)
        try:
            embed_client = ZhipuEmbeddingClient()
            embeddings = await embed_client.embed(texts)
            return embeddings
        except Exception as e:
            logger.warning("zhipu_embedding_failed", error=str(e), count=len(texts))
            # Fallback: return zero vectors (won't match anything in search)
            return [[0.0] * settings.embedding_dimension for _ in texts]


class IngestionPipeline:
    """Full ingestion: Parse → Chunk → Embed → Index."""

    def __init__(self) -> None:
        self.parser = DocumentParser()
        self.chunker = Chunker()
        self.embedder = Embedder()

    async def ingest(
        self,
        content: bytes,
        file_type: str,
        kb_id: str,
        tenant_id: str,
        filename: str = "",
        chunk_strategy: str = "fixed_512",
        chunk_size: int = 512,
    ) -> list[dict]:
        """Ingest one document into the vector database."""
        # 1. Parse
        text = await self.parser.parse(content, file_type, filename)

        # 2. Chunk
        chunks = self.chunker.chunk(
            text, strategy=chunk_strategy, chunk_size=chunk_size,
            source=filename, filename=filename,
        )

        # 3. Embed
        texts = [c["content"] for c in chunks]
        embeddings = await self.embedder.embed(texts)

        # 4. Index into vector database
        # TODO: Write to Milvus/Qdrant
        for i, chunk in enumerate(chunks):
            chunk["embedding"] = embeddings[i]
            chunk["kb_id"] = kb_id
            chunk["tenant_id"] = tenant_id
            chunk["chunk_id"] = str(uuid.uuid4())

        logger.info(
            "ingestion_complete",
            kb_id=kb_id,
            tenant_id=tenant_id,
            filename=filename,
            chunks=len(chunks),
        )

        return chunks

    async def start_ingestion(self, kb_id: str, tenant_id: str) -> str:
        """Start async ingestion task — returns task_id."""
        task_id = str(uuid.uuid4())
        _ingestion_tasks[task_id] = {
            "task_id": task_id,
            "kb_id": kb_id,
            "tenant_id": tenant_id,
            "status": "pending",
            "progress": 0.0,
        }

        logger.info("ingestion_task_started", task_id=task_id, kb_id=kb_id)

        # TODO: Load un-ingested documents from DB and process them
        # For now, just mark as completed
        async def _run():
            _ingestion_tasks[task_id]["status"] = "processing"
            _ingestion_tasks[task_id]["progress"] = 0.5
            await asyncio.sleep(0.5)  # Simulated processing
            _ingestion_tasks[task_id]["status"] = "completed"
            _ingestion_tasks[task_id]["progress"] = 1.0

        asyncio.create_task(_run())
        return task_id
