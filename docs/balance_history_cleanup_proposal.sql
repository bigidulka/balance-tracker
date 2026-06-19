-- Balance history cleanup proposal for prod PnL pollution.
-- DO NOT RUN without DB backup + explicit approval.
-- Scope: org 3 EVM wallet rows where persisted total_usd conflicts with assets sum.

BEGIN;

-- 1) Preview suspect rows.
WITH suspect AS (
    SELECT
        id,
        organization_id,
        service,
        integration_id,
        total_usd,
        created_at,
        (
            SELECT COALESCE(SUM((asset->>'value_usd')::double precision), 0.0)
            FROM jsonb_array_elements(assets::jsonb) AS asset
        ) AS assets_sum
    FROM balance_history
    WHERE organization_id = 3
      AND service IN (
          'evm_0xf9095877f93603d0b6c44e5a82db5dc751b34cd8',
          'evm_0x68be7703370c4134d74a45bf0eb9f44a541efb92'
      )
)
SELECT
    id,
    service,
    integration_id,
    ROUND(total_usd::numeric, 2) AS total_usd,
    ROUND(assets_sum::numeric, 2) AS assets_sum,
    ROUND((total_usd - assets_sum)::numeric, 2) AS mismatch,
    created_at
FROM suspect
WHERE ABS(total_usd - assets_sum) > GREATEST(5.0, GREATEST(ABS(total_usd), ABS(assets_sum)) * 0.05)
ORDER BY ABS(total_usd - assets_sum) DESC, created_at DESC;

-- 2) Delete only rows matching same objective mismatch rule.
-- Uncomment after preview + backup.
-- WITH suspect AS (
--     SELECT
--         id,
--         total_usd,
--         (
--             SELECT COALESCE(SUM((asset->>'value_usd')::double precision), 0.0)
--             FROM jsonb_array_elements(assets::jsonb) AS asset
--         ) AS assets_sum
--     FROM balance_history
--     WHERE organization_id = 3
--       AND service IN (
--           'evm_0xf9095877f93603d0b6c44e5a82db5dc751b34cd8',
--           'evm_0x68be7703370c4134d74a45bf0eb9f44a541efb92'
--       )
-- )
-- DELETE FROM balance_history bh
-- USING suspect s
-- WHERE bh.id = s.id
--   AND ABS(s.total_usd - s.assets_sum) > GREATEST(5.0, GREATEST(ABS(s.total_usd), ABS(s.assets_sum)) * 0.05);

ROLLBACK;
