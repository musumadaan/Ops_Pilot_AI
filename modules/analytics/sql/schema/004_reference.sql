-- Reference data: every exception code the views can emit, with severity and the human next step.
CREATE TABLE ops.exception_code (
    code               text PRIMARY KEY,
    severity           text NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    description        text NOT NULL,
    recommended_action text NOT NULL
);

INSERT INTO ops.exception_code (code, severity, description, recommended_action) VALUES
    ('NO_PO', 'error', 'Invoice has no purchase order reference', 'Request a PO or route to the budget owner for retroactive approval'),
    ('NO_RECEIPT', 'error', 'No goods receipt recorded for an invoiced PO line', 'Confirm delivery with the requester before paying'),
    ('QTY_OVER_RECEIPT', 'error', 'Cumulative invoiced quantity exceeds received quantity', 'Pay only the received quantity; ask the supplier for a credit note'),
    ('OVER_ORDERED_QTY', 'error', 'Cumulative invoiced quantity exceeds ordered quantity', 'Hold; check for a duplicate or an unapproved PO change'),
    ('PRICE_VARIANCE', 'error', 'Unit price differs from the PO beyond tolerance', 'Compare with the contract; request a corrected invoice or approve the variance'),
    ('DUPLICATE_INVOICE_NUMBER', 'error', 'Same supplier and normalised invoice number already received', 'Hold and compare with the earlier invoice; do not pay twice'),
    ('POSSIBLE_DUPLICATE', 'warning', 'Same supplier and total within the duplicate window under a different number', 'Compare line items and dates with the earlier invoice'),
    ('DATE_BEFORE_PO', 'warning', 'Invoice dated before the purchase order', 'Check whether work started without a PO'),
    ('BANK_ACCOUNT_CHANGED', 'error', 'Bank account differs from the supplier''s previous invoice', 'Verify the change by calling a known supplier contact before paying'),
    ('SPLIT_BELOW_THRESHOLD', 'warning', 'Several invoices just under the approval threshold in a short window', 'Review combined value against the approval policy'),
    ('UNMATCHED_LINE', 'warning', 'Invoice line could not be linked to a PO line', 'Link the line manually or request clarification'),
    ('WEEKEND_POSTING', 'info', 'Invoice received on a weekend (business time zone)', 'No action unless combined with other flags'),
    ('ROUND_AMOUNT', 'info', 'Invoice total is an exact multiple of the round-amount step', 'No action unless combined with other flags');
