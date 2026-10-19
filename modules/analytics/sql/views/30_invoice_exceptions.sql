-- One row per (invoice, exception code). All thresholds come from
-- ops.control_parameter.
-- Rule families: line rules (three-way match), duplicates (window functions),
-- bank-account
-- change (lag), split invoices (LATERAL aggregate), and information flags.
CREATE VIEW ops.v_invoice_exceptions AS
WITH p AS (
    SELECT
        max(value) FILTER (WHERE key = 'qty_tolerance_abs') AS qty_tol,
        max(value) FILTER (WHERE key = 'approval_threshold') AS threshold,
        max(value) FILTER (WHERE key = 'split_band_low_ratio') AS band,
        max(value) FILTER (WHERE key = 'split_window_days') AS split_days,
        max(value) FILTER (WHERE key = 'duplicate_window_days') AS dup_days,
        max(value) FILTER (WHERE key = 'round_amount_step') AS round_step
    FROM ops.control_parameter
),

live AS (
    SELECT
        i.*,
        upper(regexp_replace(i.invoice_number, '[^A-Za-z0-9]', '', 'g'))
            AS norm_number
    FROM ops.invoice AS i
    WHERE i.status <> 'rejected'
),

line_rules AS (
    SELECT
        m.invoice_id,
        'NO_RECEIPT' AS code,
        format(
            'Line %s: no goods receipt for %s line %s',
            m.line_no,
            m.po_number,
            m.po_line_no
        ) AS detail
    FROM ops.v_invoice_line_match AS m
    WHERE m.po_line_no IS NOT NULL AND m.qty_received = 0
    UNION ALL
    SELECT
        m.invoice_id,
        'QTY_OVER_RECEIPT' AS code,
        format(
            'Line %s: invoiced %s cumulative vs received %s',
            m.line_no,
            m.qty_invoiced_cum,
            m.qty_received
        ) AS detail
    FROM ops.v_invoice_line_match AS m CROSS JOIN p
    WHERE m.qty_received > 0 AND m.qty_invoiced_cum > m.qty_received + p.qty_tol
    UNION ALL
    SELECT
        m.invoice_id,
        'OVER_ORDERED_QTY' AS code,
        format(
            'Line %s: invoiced %s cumulative vs ordered %s',
            m.line_no,
            m.qty_invoiced_cum,
            m.qty_ordered
        ) AS detail
    FROM ops.v_invoice_line_match AS m
    WHERE m.qty_ordered IS NOT NULL AND m.qty_invoiced_cum > m.qty_ordered
    UNION ALL
    SELECT
        m.invoice_id,
        'PRICE_VARIANCE' AS code,
        format(
            'Line %s: price %s vs PO %s (%s%%, tolerance %s%%)',
            m.line_no,
            m.unit_price,
            m.po_unit_price,
            m.price_variance_pct,
            m.price_tolerance_pct
        ) AS detail
    FROM ops.v_invoice_line_match AS m
    WHERE abs(m.price_variance_pct) > m.price_tolerance_pct
),

header_rules AS (
    SELECT
        i.invoice_id,
        'NO_PO' AS code,
        'Invoice carries no PO reference' AS detail
    FROM live AS i
    WHERE i.po_number IS NULL
    UNION ALL
    SELECT
        i.invoice_id,
        'UNMATCHED_LINE' AS code,
        format('Line %s could not be linked to a PO line', il.line_no) AS detail
    FROM live AS i
    INNER JOIN ops.invoice_line AS il ON i.invoice_id = il.invoice_id
    WHERE i.po_number IS NOT NULL AND il.po_line_no IS NULL
    UNION ALL
    SELECT
        i.invoice_id,
        'DATE_BEFORE_PO' AS code,
        format(
            'Invoice date %s is before PO date %s',
            i.invoice_date,
            po.order_date
        ) AS detail
    FROM live AS i
    INNER JOIN ops.purchase_order AS po ON i.po_number = po.po_number
    WHERE i.invoice_date < po.order_date
    UNION ALL
    SELECT
        i.invoice_id,
        'WEEKEND_POSTING' AS code,
        format(
            'Received %s (business time zone)',
            to_char(
                i.received_at AT TIME ZONE 'America/Edmonton', 'Dy YYYY-MM-DD'
            )
        ) AS detail
    FROM live AS i
    WHERE
        extract(ISODOW FROM i.received_at AT TIME ZONE 'America/Edmonton') IN (
            6, 7
        )
    UNION ALL
    SELECT
        i.invoice_id,
        'ROUND_AMOUNT' AS code,
        format('Total %s is a multiple of %s', i.total, p.round_step) AS detail
    FROM live AS i CROSS JOIN p
    WHERE i.total >= p.round_step AND mod(i.total, p.round_step) = 0
),

dup_number AS (
    SELECT
        invoice_id,
        invoice_number,
        row_number()
            OVER (
                PARTITION BY supplier_id, norm_number
                ORDER BY received_at, invoice_id
            )
            AS rn,
        first_value(invoice_id)
            OVER (
                PARTITION BY supplier_id, norm_number
                ORDER BY received_at, invoice_id
            )
            AS first_invoice_id
    FROM live
),

dup_amount AS (
    SELECT
        invoice_id,
        norm_number,
        invoice_date - lag(invoice_date) OVER w AS gap_days,
        lag(norm_number) OVER w AS prev_norm,
        lag(invoice_id) OVER w AS prev_invoice_id
    FROM live
    WINDOW
        w AS (
            PARTITION BY supplier_id, total
            ORDER BY invoice_date, received_at, invoice_id
        )
),

bank AS (
    SELECT
        invoice_id,
        bank_account_id,
        lag(bank_account_id) OVER (
            PARTITION BY supplier_id
            ORDER BY invoice_date, received_at, invoice_id
        ) AS prev_account
    FROM live
    WHERE bank_account_id IS NOT NULL
),

split AS (
    SELECT
        i.invoice_id,
        near.n_near,
        near.sum_near
    FROM live AS i
    CROSS JOIN p
    CROSS JOIN
        LATERAL (
            SELECT
                count(*) AS n_near,
                sum(o.total) AS sum_near
            FROM live AS o
            WHERE
                o.supplier_id = i.supplier_id
                AND o.invoice_date BETWEEN i.invoice_date
                - p.split_days::int AND i.invoice_date
                AND o.total >= p.band * p.threshold AND o.total < p.threshold
        ) AS near
    WHERE i.total >= p.band * p.threshold AND i.total < p.threshold
),

pattern_rules AS (
    SELECT
        invoice_id,
        'DUPLICATE_INVOICE_NUMBER' AS code,
        format(
            'Number %s repeats earlier invoice %s',
            invoice_number,
            first_invoice_id
        ) AS detail
    FROM dup_number
    WHERE rn > 1
    UNION ALL
    SELECT
        d.invoice_id,
        'POSSIBLE_DUPLICATE' AS code,
        format(
            'Same supplier and total as %s, %s days earlier',
            d.prev_invoice_id,
            d.gap_days
        ) AS detail
    FROM dup_amount AS d CROSS JOIN p
    WHERE
        d.gap_days <= p.dup_days AND d.norm_number IS DISTINCT FROM d.prev_norm
    UNION ALL
    SELECT
        invoice_id,
        'BANK_ACCOUNT_CHANGED' AS code,
        format(
            'Account %s differs from previous %s', bank_account_id, prev_account
        ) AS detail
    FROM bank
    WHERE prev_account IS NOT NULL AND prev_account <> bank_account_id
    UNION ALL
    SELECT
        invoice_id,
        'SPLIT_BELOW_THRESHOLD' AS code,
        format(
            '%s invoices just under the threshold within the window, '
            'combined %s',
            n_near,
            sum_near
        ) AS detail
    FROM split
    WHERE n_near >= 2
),

all_rules AS (
    SELECT * FROM line_rules
    UNION ALL
    SELECT * FROM header_rules
    UNION ALL
    SELECT * FROM pattern_rules
)

SELECT
    a.invoice_id,
    inv.supplier_id,
    a.code AS exception_code,
    ec.severity,
    count(*) AS occurrences,
    string_agg(a.detail, '; ' ORDER BY a.detail) AS detail
FROM all_rules AS a
INNER JOIN ops.invoice AS inv ON a.invoice_id = inv.invoice_id
INNER JOIN ops.exception_code AS ec ON a.code = ec.code
GROUP BY a.invoice_id, inv.supplier_id, a.code, ec.severity;
