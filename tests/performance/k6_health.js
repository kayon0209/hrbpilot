// k6 baseline smoke for PR-01
//
// This is a load-test skeleton, not a performance claim. It verifies that the
// health probe is reachable and that the endpoint shape remains stable. Any
// real throughput/latency claim must be produced from a recorded benchmark
// run with environment details.

import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: 1,
  duration: '10s',
  thresholds: {
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  const res = http.get(__ENV.BASE_URL || 'http://localhost:8001/api/health');
  check(res, {
    'health status is 200': (r) => r.status === 200,
    'health payload has status ok': (r) => r.json('status') === 'ok',
  });
  sleep(1);
}
