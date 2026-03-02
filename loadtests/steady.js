import { cfg, runReadPathSuite, threshold } from './common.js';

const steadyRps = Math.max(1, parseInt(__ENV.STEADY_RPS || '25', 10));
const steadyDuration = __ENV.STEADY_DURATION || '10m';
const preAllocatedVUs = Math.max(10, parseInt(__ENV.PRE_ALLOCATED_VUS || '40', 10));
const maxVUs = Math.max(preAllocatedVUs, parseInt(__ENV.MAX_VUS || '250', 10));

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  scenarios: {
    steady_1k_accounts: {
      executor: 'constant-arrival-rate',
      rate: steadyRps,
      timeUnit: '1s',
      duration: steadyDuration,
      preAllocatedVUs,
      maxVUs,
      tags: {
        profile: 'steady',
        org_count: String(cfg.organizationCount),
        accounts_per_org: String(cfg.accountsPerOrg),
      },
    },
  },
  thresholds: {
    http_req_failed: [threshold('THRESHOLD_HTTP_REQ_FAILED_STEADY', 'rate<0.01')],
    http_req_duration: [
      threshold('THRESHOLD_HTTP_REQ_P95_STEADY', 'p(95)<1500'),
      threshold('THRESHOLD_HTTP_REQ_P99_STEADY', 'p(99)<2500'),
    ],
    checks: [threshold('THRESHOLD_CHECKS_STEADY', 'rate>0.99')],
  },
};

export default function () {
  runReadPathSuite();
}
