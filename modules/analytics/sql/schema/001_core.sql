-- OpsPilot core operational schema (procurement / accounts payable).
-- Money: numeric, never float. No full bank account numbers are stored (synthetic data only,
-- and the real system would hold a token or the last four digits).
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE ops.supplier (
    supplier_id        text PRIMARY KEY,
    name               text NOT NULL,
    country            char(2) NOT NULL DEFAULT 'CA',
    payment_terms_days integer NOT NULL DEFAULT 30 CHECK (payment_terms_days BETWEEN 0 AND 180),
    status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'on_hold', 'inactive')),
    created_on         date NOT NULL
);

CREATE TABLE ops.supplier_bank_account (
    bank_account_id text PRIMARY KEY,
    supplier_id     text NOT NULL REFERENCES ops.supplier,
    institution     text NOT NULL,
    account_last4   char(4) NOT NULL,
    valid_from      date NOT NULL,
    valid_to        date,
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE ops.employee (
    employee_id    text PRIMARY KEY,
    name           text NOT NULL,
    role           text NOT NULL CHECK (role IN ('buyer', 'ap_analyst', 'manager', 'director', 'executive')),
    manager_id     text REFERENCES ops.employee,
    approval_limit numeric(14, 2) NOT NULL DEFAULT 0 CHECK (approval_limit >= 0)
);

CREATE TABLE ops.contract (
    contract_id         text PRIMARY KEY,
    supplier_id         text NOT NULL REFERENCES ops.supplier,
    category            text NOT NULL,
    effective_from      date NOT NULL,
    effective_to        date,
    price_tolerance_pct numeric(5, 2) NOT NULL DEFAULT 2.00 CHECK (price_tolerance_pct >= 0),
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE TABLE ops.purchase_order (
    po_number   text PRIMARY KEY,
    supplier_id text NOT NULL REFERENCES ops.supplier,
    buyer_id    text NOT NULL REFERENCES ops.employee,
    category    text NOT NULL,
    currency    char(3) NOT NULL DEFAULT 'CAD',
    order_date  date NOT NULL,
    status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
    cost_center text NOT NULL
);

CREATE TABLE ops.po_line (
    po_number   text NOT NULL REFERENCES ops.purchase_order,
    line_no     integer NOT NULL CHECK (line_no > 0),
    description text NOT NULL,
    uom         text NOT NULL DEFAULT 'EA',
    qty_ordered numeric(14, 3) NOT NULL CHECK (qty_ordered > 0),
    unit_price  numeric(14, 4) NOT NULL CHECK (unit_price >= 0),
    PRIMARY KEY (po_number, line_no)
);

CREATE TABLE ops.goods_receipt (
    receipt_id    text PRIMARY KEY,
    po_number     text NOT NULL REFERENCES ops.purchase_order,
    received_date date NOT NULL,
    received_by   text NOT NULL REFERENCES ops.employee
);

CREATE TABLE ops.receipt_line (
    receipt_id   text NOT NULL REFERENCES ops.goods_receipt,
    line_no      integer NOT NULL CHECK (line_no > 0),
    po_number    text NOT NULL,
    po_line_no   integer NOT NULL,
    qty_received numeric(14, 3) NOT NULL CHECK (qty_received >= 0),
    PRIMARY KEY (receipt_id, line_no),
    FOREIGN KEY (po_number, po_line_no) REFERENCES ops.po_line
);

-- Deliberately NOT unique on (supplier_id, invoice_number): real duplicates arrive re-keyed
-- ("INV-1001" vs "INV1001"). They are detected by ops.v_invoice_exceptions, not rejected.
CREATE TABLE ops.invoice (
    invoice_id      text PRIMARY KEY,
    supplier_id     text NOT NULL REFERENCES ops.supplier,
    invoice_number  text NOT NULL,
    po_number       text REFERENCES ops.purchase_order,
    invoice_date    date NOT NULL,
    received_at     timestamptz NOT NULL,
    approved_at     timestamptz,
    due_date        date NOT NULL,
    currency        char(3) NOT NULL DEFAULT 'CAD',
    subtotal        numeric(14, 2) NOT NULL CHECK (subtotal >= 0),
    tax             numeric(14, 2) NOT NULL DEFAULT 0 CHECK (tax >= 0),
    total           numeric(14, 2) NOT NULL CHECK (total >= 0),
    bank_account_id text REFERENCES ops.supplier_bank_account,
    status          text NOT NULL DEFAULT 'received'
        CHECK (status IN ('received', 'extracted', 'matched', 'exception', 'approved', 'paid', 'rejected')),
    source          text NOT NULL DEFAULT 'manual' CHECK (source IN ('ocr', 'edi', 'manual')),
    source_sha256   text,
    CHECK (approved_at IS NULL OR approved_at >= received_at)
);

CREATE TABLE ops.invoice_line (
    invoice_id  text NOT NULL REFERENCES ops.invoice,
    line_no     integer NOT NULL CHECK (line_no > 0),
    po_number   text,
    po_line_no  integer,
    description text NOT NULL,
    qty         numeric(14, 3) NOT NULL,
    unit_price  numeric(14, 4) NOT NULL,
    amount      numeric(14, 2) NOT NULL,
    PRIMARY KEY (invoice_id, line_no),
    FOREIGN KEY (po_number, po_line_no) REFERENCES ops.po_line
);

CREATE TABLE ops.payment (
    payment_id      text PRIMARY KEY,
    invoice_id      text NOT NULL REFERENCES ops.invoice,
    paid_date       date NOT NULL,
    amount          numeric(14, 2) NOT NULL CHECK (amount > 0),
    bank_account_id text REFERENCES ops.supplier_bank_account,
    method          text NOT NULL DEFAULT 'eft' CHECK (method IN ('eft', 'cheque', 'wire'))
);

CREATE TABLE ops.budget (
    category text NOT NULL,
    period   date NOT NULL CHECK (period = date_trunc('month', period)::date),
    amount   numeric(14, 2) NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (category, period)
);

-- Tolerances and thresholds are data, not code, so a change is a reviewed row update.
CREATE TABLE ops.control_parameter (
    key         text PRIMARY KEY,
    value       numeric NOT NULL,
    description text NOT NULL
);

INSERT INTO ops.control_parameter (key, value, description) VALUES
    ('default_price_tolerance_pct', 2.0, 'Allowed unit-price variance vs PO when no contract tolerance applies'),
    ('qty_tolerance_abs', 0.0, 'Allowed invoiced quantity above received quantity (units)'),
    ('approval_threshold', 10000, 'Invoice total at or above which a higher approval level is required'),
    ('split_band_low_ratio', 0.8, 'Invoices at 80%..100% of the threshold are candidates for split-invoice detection'),
    ('split_window_days', 7, 'Window for split-invoice detection'),
    ('duplicate_window_days', 14, 'Window for same-supplier same-total duplicate detection'),
    ('round_amount_step', 1000, 'Totals that are exact multiples of this value are flagged for information');
