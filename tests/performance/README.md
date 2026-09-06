# 性能测试骨架（PR-01：只记录基线，不承诺未压测的提升）

> **诚实声明**：本目录是**压测基线骨架**——脚本可运行、结果可复现，但**尚未在
> 生产负载下压测**。不要用这里的脚本或任何"建议 SLO"（见评审稿 §5.2）对外声称
> 已达成延迟/吞吐指标。**先记录基线，再谈提升**；任何性能数字必须附运行环境
> （机器、数据量、并发、provider）与时间戳，否则视为未测。

## 用途

- 建立可重复的负载基线：普通 API、SSE 并发、健康探针。
- 配合后端结构化分段日志（`pipeline_segment_latency` /
  `policy_qa_segment_latency` / `policy_qa_stream_segment_latency`）定位热点，
  不把分段延迟塞进 `/api/health` 或 `/api/ready` 响应。

## 工具

- **Locust**（Python，`[dev]` 依赖）：`tests/performance/locustfile.py`
- **k6**（独立二进制，**不是 Python 包，不得加入 pyproject.toml**）：
  `tests/performance/k6_health.js`

## 运行

### Locust

```bash
pip install -e ".[dev]"
locust -f tests/performance/locustfile.py --host http://localhost:8001 \
  --users 10 --spawn-rate 1 --run-time 2m --headless \
  --html tests/performance/reports/locust_$(date +%s).html
```

### k6

```bash
k6 run tests/performance/k6_health.js   # 需先安装 k6 二进制
```

## 记录基线

每次压测请记录：日期、提交 SHA、机器规格、并发、时长、外部依赖版本、LLM
provider/模型、结果文件路径。基线数据放 `tests/performance/reports/`，不入库。

## 当前状态

- [ ] 普通 API 基线（未测）
- [ ] SSE 并发基线（未测）
- [ ] 文件上传/队列峰值（未测）
- [ ] 供应商超时行为（未测）

完成任一项后更新本清单并附报告，**不得**在未压测时声称任何延迟/吞吐目标达成。