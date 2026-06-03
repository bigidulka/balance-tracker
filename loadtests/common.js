import http from 'k6/http';
import { check, sleep } from 'k6';

const DEFAULT_HEADERS = {
  'Content-Type': 'application/json',
};

function envInt(name, fallback) {
  const value = __ENV[name];
  if (value === undefined || value === '') return fallback;
  const parsed = parseInt(value, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

function envFloat(name, fallback) {
  const value = __ENV[name];
  if (value === undefined || value === '') return fallback;
  const parsed = parseFloat(value);
  return Number.isNaN(parsed) ? fallback : parsed;
}

function envBool(name, fallback = false) {
  const value = (__ENV[name] || '').toLowerCase();
  if (!value) return fallback;
  return value === '1' || value === 'true' || value === 'yes' || value === 'on';
}

function envCsvInts(name) {
  const raw = __ENV[name];
  if (!raw) return [];
  return raw
    .split(',')
    .map((item) => parseInt(item.trim(), 10))
    .filter((item) => !Number.isNaN(item));
}

export const cfg = {
  baseUrl: (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/$/, ''),
  authToken: __ENV.AUTH_TOKEN || '',
  organizationId: envInt('ORGANIZATION_ID', 1),
  organizationCount: Math.max(1, envInt('ORGANIZATION_COUNT', 1)),
  organizationIds: envCsvInts('ORGANIZATION_IDS'),
  accountsPerOrg: Math.max(1, envInt('ACCOUNTS_PER_ORG', 1000)),
  thinkTimeMs: Math.max(0, envInt('THINK_TIME_MS', 0)),
  requestTimeoutMs: Math.max(100, envInt('REQUEST_TIMEOUT_MS', 30000)),
  forceRefreshRatio: Math.max(0, Math.min(1, envFloat('FORCE_REFRESH_RATIO', 0))),
  dashboardMetricsRatio: Math.max(0, Math.min(1, envFloat('DASHBOARD_METRICS_RATIO', 0.25))),
  dashboardUtcOffsetMinutes: envInt('DASHBOARD_UTC_OFFSET_MINUTES', 180),
  enableMutatingEndpoints: envBool('ENABLE_MUTATING_ENDPOINTS', false),
};

export function threshold(name, fallback) {
  return __ENV[name] || fallback;
}

export function pickOrganizationId(vu, iter) {
  if (cfg.organizationIds.length > 0) {
    return cfg.organizationIds[(vu + iter) % cfg.organizationIds.length];
  }
  return cfg.organizationId + ((vu + iter) % cfg.organizationCount);
}

export function defaultParams(endpointName, organizationId) {
  const headers = { ...DEFAULT_HEADERS, 'X-Organization-Id': String(organizationId) };
  if (cfg.authToken) {
    headers.Authorization = `Bearer ${cfg.authToken}`;
  }
  return {
    headers,
    timeout: `${cfg.requestTimeoutMs}ms`,
    tags: {
      endpoint: endpointName,
      org_id: String(organizationId),
    },
  };
}

export function runReadPathSuite() {
  const orgId = pickOrganizationId(__VU, __ITER);

  const balancesUrl = `${cfg.baseUrl}/api/v1/balances/cached`;
  const balancesResp = http.get(balancesUrl, defaultParams('balances_cached', orgId));
  check(balancesResp, {
    'balances/cached status is 200': (r) => r.status === 200,
  });

  const includeMetrics = Math.random() < cfg.dashboardMetricsRatio;
  const dashboardUrl = `${cfg.baseUrl}/api/v1/dashboard/summary?include_metrics=${includeMetrics ? 'true' : 'false'}&utc_offset_minutes=${cfg.dashboardUtcOffsetMinutes}`;
  const dashboardResp = http.get(
    dashboardUrl,
    defaultParams(includeMetrics ? 'dashboard_summary_metrics' : 'dashboard_summary', orgId),
  );
  check(dashboardResp, {
    'dashboard/summary status is 200': (r) => r.status === 200,
  });

  const txOffset = (__ITER % 20) * 50;
  const txUrl = `${cfg.baseUrl}/api/v1/transactions?limit=50&offset=${txOffset}`;
  const txResp = http.get(txUrl, defaultParams('transactions_list', orgId));
  check(txResp, {
    'transactions status is 200': (r) => r.status === 200,
  });

  const healthUrl = `${cfg.baseUrl}/api/v1/health`;
  const healthResp = http.get(healthUrl, defaultParams('health', orgId));
  check(healthResp, {
    'health status is 200': (r) => r.status === 200,
  });

  if (Math.random() < cfg.forceRefreshRatio) {
    const refreshUrl = `${cfg.baseUrl}/api/v1/balances?force_refresh=true`;
    const refreshResp = http.get(refreshUrl, defaultParams('balances_force_refresh', orgId));
    check(refreshResp, {
      'balances force refresh status is 200': (r) => r.status === 200,
    });
  }

  if (cfg.enableMutatingEndpoints && Math.random() < 0.02) {
    const refreshTxUrl = `${cfg.baseUrl}/api/v1/transactions/refresh?since_hours=24`;
    const txRefreshResp = http.post(refreshTxUrl, null, defaultParams('transactions_refresh', orgId));
    check(txRefreshResp, {
      'transactions refresh status is 200': (r) => r.status === 200,
    });
  }

  if (cfg.thinkTimeMs > 0) {
    sleep(cfg.thinkTimeMs / 1000);
  }
}
