import { cfg, runReadPathSuite, threshold } from './common.js';

const degradedRate = Math.max(1, parseInt(__ENV.DEGRADED_RPS || '15', 10));
const preAllocatedVUs = Math.max(10, parseInt(__ENV.DEGRADED_PRE_ALLOCATED_VUS || '40', 10));
const maxVUs = Math.max(preAllocatedVUs, parseInt(__ENV.DEGRADED_MAX_VUS || '300', 10));

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  scenarios: {
    degraded_1k_accounts: {
      executor: 'constant-arrival-rate',
      rate: degradedRate,
      timeUnit: '1s',
      duration: __ENV.DEGRADED_DURATION || '12m',
      preAllocatedVUs,
      maxVUs,
      tags: {
        profile: 'degraded',
        org_count: String(cfg.organizationCount),
        accounts_per_org: String(cfg.accountsPerOrg),
      },
    },
  },
  thresholds: {
    http_req_failed: [threshold('THRESHOLD_HTTP_REQ_FAILED_DEGRADED', 'rate<0.05')],
    http_req_duration: [
      threshold('THRESHOLD_HTTP_REQ_P95_DEGRADED', 'p(95)<4000'),
      threshold('THRESHOLD_HTTP_REQ_P99_DEGRADED', 'p(99)<7000'),
    ],
    checks: [threshold('THRESHOLD_CHECKS_DEGRADED', 'rate>0.95')],
  },
};

export default function () {
  runReadPathSuite();
}
