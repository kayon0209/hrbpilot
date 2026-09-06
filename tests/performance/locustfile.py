"""Locust load-test baseline (PR-01).

Baseline skeleton only — no performance claims yet. Runs against the API as
an anonymous-probe mix plus an authenticated policy-qa question.

Usage:
    locust -f tests/performance/locustfile.py --host http://localhost:8001 \
      --users 10 --spawn-rate 1 --run-time 2m --headless
"""

from locust import HttpUser, between, task


class HealthProbe(HttpUser):
    """Liveness/readiness probes — must always answer before auth."""

    wait_time = between(0.2, 0.5)
    weight = 3

    @task
    def health(self):
        self.client.get("/api/health")

    @task
    def ready(self):
        self.client.get("/api/ready")


class PolicyQAUser(HttpUser):
    """Authenticated policy-qa ask. Requires a valid JWT in PRACTICE runs;

    by default this class is disabled (weight 0) so the skeleton works
    without secrets. Enable by setting PRACTICE_JWT in the environment and
    switching weight to 1.
    """

    wait_time = between(1, 3)
    weight = 0  # disabled by default — needs a real JWT

    def on_start(self):
        import os

        token = os.environ.get("PRACTICE_JWT", "")
        if not token:
            self.environment.runner.quit()
            raise RuntimeError("PRACTICE_JWT required to load-test /api/policy-qa")
        self.headers = {"Authorization": f"Bearer {token}"}

    @task
    def ask(self):
        self.client.post(
            "/api/policy-qa/ask",
            json={"question": "年假没休完能顺延到明年吗？", "stream": False},
            headers=self.headers,
        )
