import { cfg, runReadPathSuite, threshold } from './common.js';

const startRate = Math.max(1, parseInt(__ENV.BURST_START_RPS || '20', 10));
const peakRate = Math.max(startRate, parseInt(__ENV.BURST_PEAK_RPS || '120', 10));
const preAllocatedVUs = Math.max(20, parseInt(__ENV.BURST_PRE_ALLOCATED_VUS || '80', 10));
const maxVUs = Math.max(preAllocatedVUs, parseInt(__ENV.BURST_MAX_VUS || '500', 10));

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  scenarios: {
    burst_1k_accounts: {
      executor: 'ramping-arrival-rate',
      startRate,
      timeUnit: '1s',
      preAllocatedVUs,
      maxVUs,
      stages: [
        { target: startRate, duration: __ENV.BURST_WARMUP || '2m' },
        { target: peakRate, duration: __ENV.BURST_RAMP || '2m' },
        { target: peakRate, duration: __ENV.BURST_HOLD || '3m' },
        { target: startRate, duration: __ENV.BURST_COOLDOWN || '2m' },
      ],
      tags: {
        profile: 'burst',
        org_count: String(cfg.organizationCount),
        accounts_per_org: String(cfg.accountsPerOrg),
      },
    },
  },
  thresholds: {
    http_req_failed: [threshold('THRESHOLD_HTTP_REQ_FAILED_BURST', 'rate<0.02')],
    http_req_duration: [
      threshold('THRESHOLD_HTTP_REQ_P95_BURST', 'p(95)<2500'),
      threshold('THRESHOLD_HTTP_REQ_P99_BURST', 'p(99)<4000'),
    ],
    checks: [threshold('THRESHOLD_CHECKS_BURST', 'rate>0.98')],
  },
};

export default function () {
  runReadPathSuite();
}
