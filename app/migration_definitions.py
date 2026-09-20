"""Definicoes imutaveis das migrations de schema ja versionadas.

Novas fases devem adicionar outra versao em vez de alterar estas constantes.
"""
from __future__ import annotations

SERVICE_CATALOG_0003_STATEMENTS = ('CREATE TABLE service_categories (\n'
 '\tid INTEGER NOT NULL, \n'
 '\tname VARCHAR(120) NOT NULL, \n'
 '\tdescription VARCHAR(300), \n'
 '\tsort_order INTEGER NOT NULL, \n'
 '\tis_active BOOLEAN NOT NULL, \n'
 '\tcreated_at DATETIME NOT NULL, \n'
 '\tupdated_at DATETIME NOT NULL, \n'
 '\tcreated_by INTEGER NOT NULL, \n'
 '\tupdated_by INTEGER NOT NULL, \n'
 '\tPRIMARY KEY (id), \n'
 '\tCONSTRAINT uq_service_categories_name UNIQUE (name), \n'
 '\tFOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT, \n'
 '\tFOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT\n'
 ')',
 'CREATE INDEX ix_service_categories_is_active ON service_categories (is_active)',
 'CREATE INDEX ix_service_categories_name ON service_categories (name)',
 'CREATE INDEX ix_service_categories_sort_order ON service_categories (sort_order)',
 'CREATE TABLE services (\n'
 '\tid INTEGER NOT NULL, \n'
 '\tcode VARCHAR(40), \n'
 '\tname VARCHAR(180) NOT NULL, \n'
 '\tdescription TEXT, \n'
 '\tcategory_id INTEGER, \n'
 '\tbilling_unit VARCHAR(16) NOT NULL, \n'
 '\tis_active BOOLEAN NOT NULL, \n'
 '\tcreated_at DATETIME NOT NULL, \n'
 '\tupdated_at DATETIME NOT NULL, \n'
 '\tcreated_by INTEGER NOT NULL, \n'
 '\tupdated_by INTEGER NOT NULL, \n'
 '\tPRIMARY KEY (id), \n'
 '\tCONSTRAINT uq_services_code UNIQUE (code), \n'
 '\tCONSTRAINT ck_services_billing_unit CHECK (billing_unit in '
 "('UNIT','KG','PAIR','METER','FIXED')), \n"
 '\tFOREIGN KEY(category_id) REFERENCES service_categories (id) ON DELETE SET NULL, \n'
 '\tFOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT, \n'
 '\tFOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT\n'
 ')',
 'CREATE INDEX ix_services_billing_unit ON services (billing_unit)',
 'CREATE INDEX ix_services_category_id ON services (category_id)',
 'CREATE INDEX ix_services_code ON services (code)',
 'CREATE INDEX ix_services_created_at ON services (created_at)',
 'CREATE INDEX ix_services_is_active ON services (is_active)',
 'CREATE INDEX ix_services_name ON services (name)',
 'CREATE TABLE service_prices (\n'
 '\tid INTEGER NOT NULL, \n'
 '\tservice_id INTEGER NOT NULL, \n'
 '\tamount NUMERIC(12, 2) NOT NULL, \n'
 '\tvalid_from DATETIME NOT NULL, \n'
 '\tvalid_to DATETIME, \n'
 '\treason VARCHAR(300), \n'
 '\tcreated_at DATETIME NOT NULL, \n'
 '\tcreated_by INTEGER NOT NULL, \n'
 '\tPRIMARY KEY (id), \n'
 '\tCONSTRAINT ck_service_prices_amount CHECK (amount >= 0), \n'
 '\tFOREIGN KEY(service_id) REFERENCES services (id) ON DELETE CASCADE, \n'
 '\tFOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT\n'
 ')',
 'CREATE INDEX ix_service_prices_created_by ON service_prices (created_by)',
 'CREATE INDEX ix_service_prices_service_id ON service_prices (service_id)',
 'CREATE INDEX ix_service_prices_valid_from ON service_prices (valid_from)',
 'CREATE INDEX ix_service_prices_valid_to ON service_prices (valid_to)',
 'CREATE UNIQUE INDEX uq_service_prices_current ON service_prices (service_id) WHERE valid_to IS '
 'NULL')

BILLING_UNIT_DEFAULTS_0007 = ({'code': 'UNIT',
  'name': 'Unidade',
  'symbol': 'un',
  'quantity_behavior': 'INTEGER',
  'decimal_places': 0,
  'display_order': 10},
 {'code': 'FIXED',
  'name': 'Preço fixo',
  'symbol': '—',
  'quantity_behavior': 'FIXED_ONE',
  'decimal_places': 0,
  'display_order': 20},
 {'code': 'KG',
  'name': 'Quilograma',
  'symbol': 'kg',
  'quantity_behavior': 'DECIMAL',
  'decimal_places': 3,
  'display_order': 30},
 {'code': 'METER',
  'name': 'Metro',
  'symbol': 'm',
  'quantity_behavior': 'DECIMAL',
  'decimal_places': 3,
  'display_order': 40},
 {'code': 'SQUARE_METER',
  'name': 'Metro quadrado',
  'symbol': 'm²',
  'quantity_behavior': 'DECIMAL',
  'decimal_places': 3,
  'display_order': 50},
 {'code': 'HOUR',
  'name': 'Hora',
  'symbol': 'h',
  'quantity_behavior': 'DECIMAL',
  'decimal_places': 3,
  'display_order': 60},
 {'code': 'DAY',
  'name': 'Diária',
  'symbol': 'dia',
  'quantity_behavior': 'INTEGER',
  'decimal_places': 0,
  'display_order': 70},
 {'code': 'SESSION',
  'name': 'Sessão',
  'symbol': 'sessão',
  'quantity_behavior': 'INTEGER',
  'decimal_places': 0,
  'display_order': 80},
 {'code': 'PAIR',
  'name': 'Par',
  'symbol': 'par',
  'quantity_behavior': 'INTEGER',
  'decimal_places': 0,
  'display_order': 90},
 {'code': 'PERSON',
  'name': 'Pessoa',
  'symbol': 'pessoa',
  'quantity_behavior': 'INTEGER',
  'decimal_places': 0,
  'display_order': 100},
 {'code': 'KM',
  'name': 'Quilômetro',
  'symbol': 'km',
  'quantity_behavior': 'DECIMAL',
  'decimal_places': 3,
  'display_order': 110},
 {'code': 'LITER',
  'name': 'Litro',
  'symbol': 'L',
  'quantity_behavior': 'DECIMAL',
  'decimal_places': 3,
  'display_order': 120},
 {'code': 'PACKAGE',
  'name': 'Pacote',
  'symbol': 'pct',
  'quantity_behavior': 'INTEGER',
  'decimal_places': 0,
  'display_order': 130})

BILLING_UNITS_0007_STATEMENTS = ('CREATE TABLE billing_units (\n'
 '\tid INTEGER NOT NULL, \n'
 '\tcode VARCHAR(40) NOT NULL, \n'
 '\tname VARCHAR(120) NOT NULL, \n'
 '\tsymbol VARCHAR(20) NOT NULL, \n'
 '\tquantity_behavior VARCHAR(16) NOT NULL, \n'
 '\tdecimal_places INTEGER NOT NULL, \n'
 '\tis_active BOOLEAN NOT NULL, \n'
 '\tdisplay_order INTEGER NOT NULL, \n'
 '\tcreated_at DATETIME NOT NULL, \n'
 '\tupdated_at DATETIME NOT NULL, \n'
 '\tPRIMARY KEY (id), \n'
 '\tCONSTRAINT uq_billing_units_code UNIQUE (code), \n'
 '\tCONSTRAINT ck_billing_units_code_format CHECK (length(code) between 1 and 40 and code = '
 "trim(code) and code = upper(code) and code not glob '*[^A-Z0-9_]*'), \n"
 '\tCONSTRAINT ck_billing_units_name CHECK (length(trim(name)) between 1 and 120), \n'
 '\tCONSTRAINT ck_billing_units_symbol CHECK (length(trim(symbol)) between 1 and 20), \n'
 '\tCONSTRAINT ck_billing_units_quantity_behavior CHECK (quantity_behavior in '
 "('INTEGER','DECIMAL','FIXED_ONE')), \n"
 "\tCONSTRAINT ck_billing_units_decimal_places CHECK (typeof(decimal_places) = 'integer' and "
 "((quantity_behavior in ('INTEGER','FIXED_ONE') and decimal_places = 0) or (quantity_behavior = "
 "'DECIMAL' and decimal_places between 1 and 6))), \n"
 "\tCONSTRAINT ck_billing_units_is_active CHECK (typeof(is_active) = 'integer' and is_active in "
 '(0, 1)), \n'
 "\tCONSTRAINT ck_billing_units_display_order CHECK (typeof(display_order) = 'integer' and "
 'display_order between 0 and 9999)\n'
 ')',
 'CREATE INDEX ix_billing_units_active_order ON billing_units (is_active, display_order)',
 'CREATE INDEX ix_billing_units_name ON billing_units (name)',
 'CREATE INDEX ix_billing_units_quantity_behavior ON billing_units (quantity_behavior)')

SERVICE_NOTES_0008_STATEMENTS = ('CREATE TABLE service_notes (\n'
 '\tid INTEGER NOT NULL, \n'
 '\trevision INTEGER NOT NULL, \n'
 '\tnumber_original VARCHAR(80) NOT NULL, \n'
 '\tnumber_normalized VARCHAR(80) NOT NULL, \n'
 '\tseries_original VARCHAR(80), \n'
 '\tseries_normalized VARCHAR(160) NOT NULL, \n'
 '\tcustomer_id INTEGER NOT NULL, \n'
 '\treceived_at DATETIME NOT NULL, \n'
 '\texpected_ready_at DATETIME NOT NULL, \n'
 '\toperational_status VARCHAR(20) NOT NULL, \n'
 '\tfinancial_status VARCHAR(16) NOT NULL, \n'
 '\tfinancial_settlement_reason VARCHAR(24), \n'
 '\tdelivery_enabled BOOLEAN NOT NULL, \n'
 '\tdelivery_amount_cents INTEGER NOT NULL, \n'
 '\tdiscount_type VARCHAR(16), \n'
 '\tdiscount_input VARCHAR(40), \n'
 '\tdiscount_base_cents INTEGER NOT NULL, \n'
 '\tdiscount_amount_cents INTEGER NOT NULL, \n'
 '\tservices_subtotal_cents INTEGER NOT NULL, \n'
 '\ttotal_cents INTEGER NOT NULL, \n'
 '\tnotes TEXT, \n'
 '\tready_at DATETIME, \n'
 '\tready_delay_days INTEGER, \n'
 '\tcanceled_at DATETIME, \n'
 '\tcanceled_by INTEGER, \n'
 '\tcancellation_reason VARCHAR(500), \n'
 '\tcreated_at DATETIME NOT NULL, \n'
 '\tupdated_at DATETIME NOT NULL, \n'
 '\tcreated_by INTEGER NOT NULL, \n'
 '\tupdated_by INTEGER NOT NULL, \n'
 '\tPRIMARY KEY (id), \n'
 '\tCONSTRAINT uq_service_notes_number_series UNIQUE (number_normalized, series_normalized), \n'
 '\tCONSTRAINT ck_service_notes_number_normalization CHECK (length(number_original) between 1 and '
 '80 and length(number_normalized) between 1 and 80 and number_normalized = '
 'trim(number_original)), \n'
 '\tCONSTRAINT ck_service_notes_series_shape CHECK (length(series_normalized) <= 160 and '
 'series_normalized = trim(series_normalized) and (series_original is null or '
 '(length(series_original) between 1 and 80 and series_original = trim(series_original)))), \n'
 '\tCONSTRAINT ck_service_notes_operational_status CHECK (operational_status in '
 "('RECEBIDO','EM_ANDAMENTO','PRONTO','ENTREGUE','CANCELADO')), \n"
 '\tCONSTRAINT ck_service_notes_financial_status CHECK (financial_status in '
 "('PENDENTE','PAGO')), \n"
 "\tCONSTRAINT ck_service_notes_revision CHECK (typeof(revision) = 'integer' and revision between "
 '1 and 9223372036854775807), \n'
 '\tCONSTRAINT ck_service_notes_expected_after_received CHECK (expected_ready_at >= '
 'received_at), \n'
 "\tCONSTRAINT ck_service_notes_delivery_enabled CHECK (typeof(delivery_enabled) = 'integer' and "
 'delivery_enabled in (0, 1)), \n'
 "\tCONSTRAINT ck_service_notes_delivery_amount CHECK (typeof(delivery_amount_cents) = 'integer' "
 'and delivery_amount_cents between 0 and 9223372036854775807 and (delivery_enabled = 1 or '
 'delivery_amount_cents = 0)), \n'
 '\tCONSTRAINT ck_service_notes_discount_type CHECK (discount_type is null or discount_type in '
 "('VALOR','PERCENTUAL')), \n"
 '\tCONSTRAINT ck_service_notes_discount_input_format CHECK (discount_input is null or '
 '(length(discount_input) between 1 and 40 and discount_input = trim(discount_input) and '
 "discount_input not glob '*[^0-9.]*' and discount_input not like '.%' and discount_input not like "
 "'%.' and length(discount_input) - length(replace(discount_input, '.', '')) <= 1)), \n"
 '\tCONSTRAINT ck_service_notes_discount_consistency CHECK ((discount_type is null and '
 'discount_input is null and discount_amount_cents = 0) or (discount_type is not null and '
 "discount_type in ('VALOR','PERCENTUAL') and discount_input is not null)), \n"
 "\tCONSTRAINT ck_service_notes_totals CHECK (typeof(services_subtotal_cents) = 'integer' and "
 "typeof(discount_base_cents) = 'integer' and typeof(discount_amount_cents) = 'integer' and "
 "typeof(total_cents) = 'integer' and services_subtotal_cents between 0 and 9223372036854775807 "
 'and discount_base_cents between 0 and 9223372036854775807 and discount_amount_cents between 0 '
 'and 9223372036854775807 and total_cents between 0 and 9223372036854775807 and '
 'discount_base_cents = services_subtotal_cents and discount_amount_cents <= discount_base_cents '
 'and total_cents = services_subtotal_cents - discount_amount_cents + delivery_amount_cents), \n'
 "\tCONSTRAINT ck_service_notes_financial_consistency CHECK ((financial_status = 'PENDENTE' and "
 "total_cents > 0 and financial_settlement_reason is null) or (financial_status = 'PAGO' and "
 'financial_settlement_reason is not null and ((total_cents = 0 and financial_settlement_reason = '
 "'ZERO_TOTAL') or (total_cents > 0 and financial_settlement_reason = 'PAYMENT')))), \n"
 '\tCONSTRAINT ck_service_notes_ready_metadata CHECK (((ready_at is null and ready_delay_days is '
 'null) or (ready_at is not null and ready_delay_days is not null and typeof(ready_delay_days) = '
 "'integer' and ready_delay_days between 0 and 3652059)) and (operational_status not in "
 "('PRONTO','ENTREGUE') or ready_at is not null) and (operational_status not in "
 "('RECEBIDO','EM_ANDAMENTO') or ready_at is null)), \n"
 "\tCONSTRAINT ck_service_notes_cancellation_metadata CHECK ((operational_status = 'CANCELADO' and "
 'canceled_at is not null and canceled_by is not null and cancellation_reason is not null and '
 "length(trim(cancellation_reason)) between 1 and 500) or (operational_status <> 'CANCELADO' and "
 'canceled_at is null and canceled_by is null and cancellation_reason is null)), \n'
 '\tFOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE RESTRICT, \n'
 '\tFOREIGN KEY(canceled_by) REFERENCES users (id) ON DELETE RESTRICT, \n'
 '\tFOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT, \n'
 '\tFOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT\n'
 ')',
 'CREATE INDEX ix_service_notes_created_at ON service_notes (created_at)',
 'CREATE INDEX ix_service_notes_customer_id ON service_notes (customer_id)',
 'CREATE INDEX ix_service_notes_expected_ready_at ON service_notes (expected_ready_at)',
 'CREATE INDEX ix_service_notes_financial_received ON service_notes (financial_status, '
 'received_at)',
 'CREATE INDEX ix_service_notes_financial_status ON service_notes (financial_status)',
 'CREATE INDEX ix_service_notes_operational_expected ON service_notes (operational_status, '
 'expected_ready_at)',
 'CREATE INDEX ix_service_notes_operational_status ON service_notes (operational_status)',
 'CREATE INDEX ix_service_notes_received_at ON service_notes (received_at)',
 'CREATE TABLE service_note_items (\n'
 '\tid INTEGER NOT NULL, \n'
 '\tnote_id INTEGER NOT NULL, \n'
 '\tservice_id INTEGER NOT NULL, \n'
 '\tservice_code_snapshot VARCHAR(40), \n'
 '\tservice_name_snapshot VARCHAR(180) NOT NULL, \n'
 '\tservice_description_snapshot TEXT, \n'
 '\tservice_category_name_snapshot VARCHAR(120), \n'
 '\tbilling_unit_id INTEGER NOT NULL, \n'
 '\tbilling_unit_code_snapshot VARCHAR(40) NOT NULL, \n'
 '\tbilling_unit_name_snapshot VARCHAR(120) NOT NULL, \n'
 '\tbilling_unit_symbol_snapshot VARCHAR(20) NOT NULL, \n'
 '\tquantity_behavior_snapshot VARCHAR(16) NOT NULL, \n'
 '\tdecimal_places_snapshot INTEGER NOT NULL, \n'
 '\tquantity_scaled INTEGER NOT NULL, \n'
 '\tunit_price_cents INTEGER NOT NULL, \n'
 '\tsubtotal_cents INTEGER NOT NULL, \n'
 '\tposition INTEGER NOT NULL, \n'
 '\tcreated_at DATETIME NOT NULL, \n'
 '\tPRIMARY KEY (id), \n'
 '\tCONSTRAINT uq_service_note_items_position UNIQUE (note_id, position), \n'
 '\tCONSTRAINT ck_service_note_items_service_name CHECK (length(trim(service_name_snapshot)) '
 'between 1 and 180), \n'
 '\tCONSTRAINT ck_service_note_items_unit_snapshot CHECK (length(billing_unit_code_snapshot) '
 'between 1 and 40 and length(trim(billing_unit_name_snapshot)) between 1 and 120 and '
 'length(trim(billing_unit_symbol_snapshot)) between 1 and 20), \n'
 '\tCONSTRAINT ck_service_note_items_quantity_behavior CHECK (quantity_behavior_snapshot in '
 "('INTEGER','DECIMAL','FIXED_ONE')), \n"
 "\tCONSTRAINT ck_service_note_items_quantity CHECK (typeof(quantity_scaled) = 'integer' and "
 "typeof(decimal_places_snapshot) = 'integer' and quantity_scaled between 1 and 999999999999999999 "
 "and ((quantity_behavior_snapshot = 'INTEGER' and decimal_places_snapshot = 0) or "
 "(quantity_behavior_snapshot = 'FIXED_ONE' and decimal_places_snapshot = 0 and quantity_scaled = "
 "1) or (quantity_behavior_snapshot = 'DECIMAL' and decimal_places_snapshot between 1 and 6))), \n"
 "\tCONSTRAINT ck_service_note_items_money CHECK (typeof(unit_price_cents) = 'integer' and "
 "typeof(subtotal_cents) = 'integer' and unit_price_cents between 0 and 9223372036854775807 and "
 'subtotal_cents between 0 and 9223372036854775807), \n'
 "\tCONSTRAINT ck_service_note_items_position CHECK (typeof(position) = 'integer' and position "
 'between 0 and 9999), \n'
 '\tFOREIGN KEY(note_id) REFERENCES service_notes (id) ON DELETE CASCADE, \n'
 '\tFOREIGN KEY(service_id) REFERENCES services (id) ON DELETE RESTRICT, \n'
 '\tFOREIGN KEY(billing_unit_id) REFERENCES billing_units (id) ON DELETE RESTRICT\n'
 ')',
 'CREATE INDEX ix_service_note_items_billing_unit_id ON service_note_items (billing_unit_id)',
 'CREATE INDEX ix_service_note_items_note_id ON service_note_items (note_id)',
 'CREATE INDEX ix_service_note_items_note_position ON service_note_items (note_id, position)',
 'CREATE INDEX ix_service_note_items_service_id ON service_note_items (service_id)',
 'CREATE TABLE service_note_events (\n'
 '\tid INTEGER NOT NULL, \n'
 '\tevent_uid VARCHAR(36) NOT NULL, \n'
 '\tnote_id INTEGER NOT NULL, \n'
 '\tevent_type VARCHAR(40) NOT NULL, \n'
 '\toccurred_at DATETIME NOT NULL, \n'
 '\tcreated_by INTEGER NOT NULL, \n'
 '\tdetails_json TEXT, \n'
 '\tPRIMARY KEY (id), \n'
 '\tCONSTRAINT uq_service_note_events_uid UNIQUE (event_uid), \n'
 '\tCONSTRAINT ck_service_note_events_uid CHECK (length(event_uid) = 36), \n'
 '\tCONSTRAINT ck_service_note_events_type CHECK (length(event_type) between 1 and 40 and '
 "event_type = upper(event_type) and event_type not glob '*[^A-Z0-9_]*'), \n"
 '\tFOREIGN KEY(note_id) REFERENCES service_notes (id) ON DELETE CASCADE, \n'
 '\tFOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT\n'
 ')',
 'CREATE INDEX ix_service_note_events_created_by ON service_note_events (created_by)',
 'CREATE INDEX ix_service_note_events_event_type ON service_note_events (event_type)',
 'CREATE INDEX ix_service_note_events_note_id ON service_note_events (note_id)',
 'CREATE INDEX ix_service_note_events_note_occurred ON service_note_events (note_id, occurred_at)',
 'CREATE INDEX ix_service_note_events_occurred_at ON service_note_events (occurred_at)')


# Fase 3. Estas instrucoes sao deliberadamente literais: migrations aplicadas
# nunca passam a depender do DDL que models futuros venham a gerar.
CASH_PAYMENT_METHOD_KIND_0009_STATEMENT = (
    "ALTER TABLE cash_payment_methods ADD COLUMN method_kind VARCHAR(16) "
    "DEFAULT 'OTHER' NOT NULL CONSTRAINT ck_cash_payment_methods_kind "
    "CHECK (method_kind in ('CASH','PIX','CARD','BOLETO','OTHER'))"
)
CASH_PAYMENT_METHOD_KIND_INDEX_0009_STATEMENT = (
    "CREATE INDEX ix_cash_payment_methods_method_kind "
    "ON cash_payment_methods (method_kind)"
)
CASH_PAYMENT_METHODS_0009_CREATE_STATEMENTS = (
    """CREATE TABLE cash_payment_methods (
        id INTEGER NOT NULL,
        name VARCHAR(80) NOT NULL,
        method_kind VARCHAR(16) DEFAULT 'OTHER' NOT NULL,
        sort_order INTEGER NOT NULL,
        is_active BOOLEAN NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_cash_payment_methods_name UNIQUE (name),
        CONSTRAINT ck_cash_payment_methods_kind CHECK (method_kind in ('CASH','PIX','CARD','BOLETO','OTHER'))
    )""",
    "CREATE INDEX ix_cash_payment_methods_is_active ON cash_payment_methods (is_active)",
    "CREATE INDEX ix_cash_payment_methods_method_kind ON cash_payment_methods (method_kind)",
    "CREATE INDEX ix_cash_payment_methods_name ON cash_payment_methods (name)",
    "CREATE INDEX ix_cash_payment_methods_sort_order ON cash_payment_methods (sort_order)",
)
PAYMENT_METHOD_DEFAULTS_0009 = (
    ("Dinheiro", "CASH", 10),
    ("Pix", "PIX", 20),
    ("Cartão", "CARD", 30),
    ("Boleto", "BOLETO", 40),
    ("Outro", "OTHER", 50),
)

PAYMENT_CONFIGURATION_0009_STATEMENTS = (
    """CREATE TABLE payment_terminals (
        id INTEGER NOT NULL,
        code VARCHAR(40) NOT NULL,
        name VARCHAR(120) NOT NULL,
        description VARCHAR(300),
        sort_order INTEGER NOT NULL,
        is_active BOOLEAN NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        created_by INTEGER NOT NULL,
        updated_by INTEGER NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_payment_terminals_code UNIQUE (code),
        CONSTRAINT ck_payment_terminals_code CHECK (length(code) between 1 and 40 and code = trim(code) and code = upper(code) and code not glob '*[^A-Z0-9_]*'),
        CONSTRAINT ck_payment_terminals_name CHECK (length(trim(name)) between 1 and 120),
        CONSTRAINT ck_payment_terminals_sort_order CHECK (typeof(sort_order) = 'integer' and sort_order between 0 and 9999),
        CONSTRAINT ck_payment_terminals_is_active CHECK (typeof(is_active) = 'integer' and is_active in (0, 1)),
        FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT,
        FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_payment_terminals_active_order ON payment_terminals (is_active, sort_order)",
    "CREATE INDEX ix_payment_terminals_created_by ON payment_terminals (created_by)",
    "CREATE INDEX ix_payment_terminals_name ON payment_terminals (name)",
    "CREATE INDEX ix_payment_terminals_updated_by ON payment_terminals (updated_by)",
    """CREATE TABLE payment_fee_rules (
        id INTEGER NOT NULL,
        payment_method_id INTEGER NOT NULL,
        terminal_id INTEGER,
        card_mode VARCHAR(12),
        installments INTEGER,
        fee_percentage_scaled INTEGER NOT NULL,
        fixed_fee_cents INTEGER NOT NULL,
        valid_from DATETIME NOT NULL,
        valid_until DATETIME,
        is_active BOOLEAN NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        created_by INTEGER NOT NULL,
        updated_by INTEGER NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_payment_fee_rules_card_mode CHECK (card_mode is null or card_mode in ('DEBIT','CREDIT')),
        CONSTRAINT ck_payment_fee_rules_installments CHECK ((card_mode is null and installments is null) or (card_mode = 'DEBIT' and installments is null) or (card_mode = 'CREDIT' and (installments is null or (typeof(installments) = 'integer' and installments between 1 and 999)))),
        CONSTRAINT ck_payment_fee_rules_percentage CHECK (typeof(fee_percentage_scaled) = 'integer' and fee_percentage_scaled between 0 and 1000000),
        CONSTRAINT ck_payment_fee_rules_fixed_fee CHECK (typeof(fixed_fee_cents) = 'integer' and fixed_fee_cents between 0 and 9223372036854775807),
        CONSTRAINT ck_payment_fee_rules_validity CHECK (valid_until is null or valid_until > valid_from),
        CONSTRAINT ck_payment_fee_rules_is_active CHECK (typeof(is_active) = 'integer' and is_active in (0, 1)),
        FOREIGN KEY(payment_method_id) REFERENCES cash_payment_methods (id) ON DELETE RESTRICT,
        FOREIGN KEY(terminal_id) REFERENCES payment_terminals (id) ON DELETE RESTRICT,
        FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT,
        FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_payment_fee_rules_active_method ON payment_fee_rules (payment_method_id, is_active)",
    "CREATE INDEX ix_payment_fee_rules_created_by ON payment_fee_rules (created_by)",
    "CREATE INDEX ix_payment_fee_rules_resolution ON payment_fee_rules (payment_method_id, terminal_id, card_mode, installments, valid_from)",
    "CREATE INDEX ix_payment_fee_rules_updated_by ON payment_fee_rules (updated_by)",
)

PAYMENTS_0010_STATEMENTS = (
    """CREATE TABLE payments (
        id INTEGER NOT NULL,
        request_uid VARCHAR(36) NOT NULL,
        service_note_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        payment_method_id INTEGER NOT NULL,
        terminal_id INTEGER,
        fee_rule_id INTEGER,
        status VARCHAR(12) NOT NULL,
        method_name_snapshot VARCHAR(80) NOT NULL,
        method_kind_snapshot VARCHAR(16) NOT NULL,
        terminal_name_snapshot VARCHAR(120),
        card_mode_snapshot VARCHAR(12),
        installments INTEGER,
        gross_amount_cents INTEGER NOT NULL,
        fee_percentage_scaled INTEGER NOT NULL,
        fixed_fee_cents INTEGER NOT NULL,
        fee_amount_cents INTEGER NOT NULL,
        net_amount_cents INTEGER NOT NULL,
        paid_at DATETIME NOT NULL,
        created_by INTEGER NOT NULL,
        created_at DATETIME NOT NULL,
        reversed_at DATETIME,
        reversed_by INTEGER,
        reversal_reason VARCHAR(500),
        PRIMARY KEY (id),
        CONSTRAINT uq_payments_request_uid UNIQUE (request_uid),
        CONSTRAINT ck_payments_request_uid CHECK (length(request_uid) = 36 and request_uid = lower(request_uid) and substr(request_uid, 9, 1) = '-' and substr(request_uid, 14, 1) = '-' and substr(request_uid, 19, 1) = '-' and substr(request_uid, 24, 1) = '-' and request_uid not glob '*[^0-9a-f-]*'),
        CONSTRAINT ck_payments_status CHECK (status in ('CONFIRMED','REVERSED')),
        CONSTRAINT ck_payments_method_snapshot CHECK (length(trim(method_name_snapshot)) between 1 and 80 and method_kind_snapshot in ('CASH','PIX','CARD','BOLETO','OTHER')),
        CONSTRAINT ck_payments_terminal_snapshot CHECK ((terminal_id is null and terminal_name_snapshot is null) or (terminal_id is not null and terminal_name_snapshot is not null and length(trim(terminal_name_snapshot)) between 1 and 120)),
        CONSTRAINT ck_payments_card_details CHECK ((method_kind_snapshot <> 'CARD' and card_mode_snapshot is null and installments is null) or (method_kind_snapshot = 'CARD' and ((card_mode_snapshot = 'DEBIT' and installments = 1) or (card_mode_snapshot = 'CREDIT' and typeof(installments) = 'integer' and installments between 1 and 999)))),
        CONSTRAINT ck_payments_money CHECK (typeof(gross_amount_cents) = 'integer' and gross_amount_cents between 1 and 9223372036854775807 and typeof(fee_percentage_scaled) = 'integer' and fee_percentage_scaled between 0 and 1000000 and typeof(fixed_fee_cents) = 'integer' and fixed_fee_cents between 0 and 9223372036854775807 and typeof(fee_amount_cents) = 'integer' and fee_amount_cents between 0 and 9223372036854775807 and typeof(net_amount_cents) = 'integer' and net_amount_cents between 0 and 9223372036854775807 and fee_amount_cents <= gross_amount_cents and net_amount_cents = gross_amount_cents - fee_amount_cents),
        CONSTRAINT ck_payments_reversal CHECK ((status = 'CONFIRMED' and reversed_at is null and reversed_by is null and reversal_reason is null) or (status = 'REVERSED' and reversed_at is not null and reversed_by is not null and reversal_reason is not null and length(trim(reversal_reason)) between 1 and 500)),
        FOREIGN KEY(service_note_id) REFERENCES service_notes (id) ON DELETE RESTRICT,
        FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE RESTRICT,
        FOREIGN KEY(payment_method_id) REFERENCES cash_payment_methods (id) ON DELETE RESTRICT,
        FOREIGN KEY(terminal_id) REFERENCES payment_terminals (id) ON DELETE RESTRICT,
        FOREIGN KEY(fee_rule_id) REFERENCES payment_fee_rules (id) ON DELETE RESTRICT,
        FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT,
        FOREIGN KEY(reversed_by) REFERENCES users (id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_payments_created_by ON payments (created_by)",
    "CREATE INDEX ix_payments_customer_paid ON payments (customer_id, paid_at)",
    "CREATE INDEX ix_payments_method_paid ON payments (payment_method_id, paid_at)",
    "CREATE INDEX ix_payments_status_paid ON payments (status, paid_at)",
    "CREATE UNIQUE INDEX uq_payments_confirmed_service_note ON payments (service_note_id) WHERE status = 'CONFIRMED'",
)

CUSTOMER_ACTIVITY_SOURCES_0011_STATEMENTS = (
    "ALTER TABLE customer_activities ADD COLUMN source_id VARCHAR(100)",
    "ALTER TABLE customer_activities ADD COLUMN source_reference VARCHAR(180)",
    "ALTER TABLE customer_activities ADD COLUMN source_type VARCHAR(40) CONSTRAINT ck_customer_activities_source CHECK ((source_type is null and source_id is null) or (source_type is not null and source_id is not null and length(source_type) between 1 and 40 and source_type = upper(source_type) and source_type not glob '*[^A-Z0-9_]*' and length(trim(source_id)) between 1 and 100))",
    "CREATE UNIQUE INDEX uq_customer_activities_source ON customer_activities (customer_id, activity_type, source_type, source_id) WHERE source_type IS NOT NULL AND source_id IS NOT NULL",
)

CUSTOMER_ACTIVITIES_0011_CREATE_STATEMENTS = (
    """CREATE TABLE customer_activities (
        id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        activity_type VARCHAR(40) NOT NULL,
        occurred_at DATETIME NOT NULL,
        description VARCHAR(300) NOT NULL,
        metadata_json TEXT,
        source_type VARCHAR(40),
        source_id VARCHAR(100),
        source_reference VARCHAR(180),
        created_by INTEGER NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_customer_activities_type CHECK (activity_type in ('CUSTOMER_CREATED','CUSTOMER_UPDATED','CUSTOMER_DEACTIVATED','CUSTOMER_REACTIVATED','VISIT','NOTE','SERVICE_CREATED','SERVICE_COMPLETED')),
        CONSTRAINT ck_customer_activities_source CHECK ((source_type is null and source_id is null) or (source_type is not null and source_id is not null and length(source_type) between 1 and 40 and source_type = upper(source_type) and source_type not glob '*[^A-Z0-9_]*' and length(trim(source_id)) between 1 and 100)),
        FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE,
        FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_customer_activities_activity_type ON customer_activities (activity_type)",
    "CREATE INDEX ix_customer_activities_created_by ON customer_activities (created_by)",
    "CREATE INDEX ix_customer_activities_customer_id ON customer_activities (customer_id)",
    "CREATE INDEX ix_customer_activities_occurred_at ON customer_activities (occurred_at)",
    "CREATE UNIQUE INDEX uq_customer_activities_source ON customer_activities (customer_id, activity_type, source_type, source_id) WHERE source_type IS NOT NULL AND source_id IS NOT NULL",
)


# Fase 4. O campo de versao invalida sessoes ja emitidas quando credenciais ou
# acesso mudam. O DEFAULT literal preserva todos os usuarios existentes.
USERS_AUTH_VERSION_0012_STATEMENT = (
    "ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 1 "
    "CONSTRAINT ck_users_auth_version "
    "CHECK (typeof(auth_version) = 'integer' and auth_version >= 1)"
)

SUPPORT_GRANTS_0012_STATEMENTS = (
    """CREATE TABLE support_grants (
        id INTEGER NOT NULL,
        grant_uid VARCHAR(36) NOT NULL,
        support_user_id INTEGER NOT NULL,
        authorized_by INTEGER NOT NULL,
        starts_at DATETIME NOT NULL,
        expires_at DATETIME NOT NULL,
        purpose VARCHAR(500) NOT NULL,
        revoked_at DATETIME,
        revoked_by INTEGER,
        revocation_reason VARCHAR(500),
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_support_grants_uid UNIQUE (grant_uid),
        CONSTRAINT ck_support_grants_uid CHECK (length(grant_uid) = 36 and grant_uid glob '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]-[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
        CONSTRAINT ck_support_grants_window CHECK (expires_at > starts_at),
        CONSTRAINT ck_support_grants_purpose CHECK (length(trim(purpose)) between 1 and 500),
        CONSTRAINT ck_support_grants_revocation CHECK ((revoked_at is null and revoked_by is null and revocation_reason is null) or (revoked_at is not null and revoked_by is not null and revocation_reason is not null and length(trim(revocation_reason)) between 1 and 500)),
        FOREIGN KEY(support_user_id) REFERENCES users (id) ON DELETE RESTRICT,
        FOREIGN KEY(authorized_by) REFERENCES users (id) ON DELETE RESTRICT,
        FOREIGN KEY(revoked_by) REFERENCES users (id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_support_grants_support_user_id ON support_grants (support_user_id)",
    "CREATE INDEX ix_support_grants_authorized_by ON support_grants (authorized_by)",
    "CREATE INDEX ix_support_grants_revoked_by ON support_grants (revoked_by)",
    "CREATE INDEX ix_support_grants_window ON support_grants (starts_at, expires_at)",
)

AUDIT_INDEXES_0012_STATEMENTS = (
    "CREATE INDEX ix_audit_events_created_at ON audit_events (created_at)",
    "CREATE INDEX ix_audit_events_user_created_at ON audit_events (user_id, created_at)",
)


# Evolução offline e financeira. O rebuild usa exclusivamente o DDL histórico
# congelado de 0008, ampliando apenas o estado operacional; nenhuma coluna ou
# registro legado é descartado. As demais estruturas são novas e literais.
SERVICE_NOTES_0013_CREATE = SERVICE_NOTES_0008_STATEMENTS[0].replace(
    "CREATE TABLE service_notes (",
    "CREATE TABLE service_notes__0013_new (",
    1,
).replace(
    "('RECEBIDO','EM_ANDAMENTO','PRONTO','ENTREGUE','CANCELADO')",
    "('RECEBIDO','EM_ANDAMENTO','PRONTO','ENTREGUE','FECHADO','CANCELADO')",
    1,
).replace(
    "financial_status in ('PENDENTE','PAGO')",
    "financial_status in ('PENDENTE','PARCIAL','PAGO')",
    1,
).replace(
    "(financial_status = 'PENDENTE' and total_cents > 0 and financial_settlement_reason is null)",
    "(financial_status in ('PENDENTE','PARCIAL') and total_cents > 0 and financial_settlement_reason is null)",
    1,
)
SERVICE_NOTES_0013_INDEXES = tuple(
    statement for statement in SERVICE_NOTES_0008_STATEMENTS
    if statement.startswith("CREATE INDEX ") and " ON service_notes " in statement
)

OFFLINE_FINANCE_ADMIN_0013_STATEMENTS = (
    """CREATE TABLE admin_locks (
        id INTEGER NOT NULL PRIMARY KEY, password_hash VARCHAR(255) NOT NULL,
        version INTEGER DEFAULT 1 NOT NULL, timeout_minutes INTEGER DEFAULT 15 NOT NULL,
        configured_by INTEGER, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
        CONSTRAINT ck_admin_locks_singleton CHECK (id = 1),
        CONSTRAINT ck_admin_locks_version CHECK (version >= 1),
        CONSTRAINT ck_admin_locks_timeout_minutes CHECK (timeout_minutes between 1 and 480),
        FOREIGN KEY(configured_by) REFERENCES users (id) ON DELETE SET NULL
    )""",
    """CREATE TABLE outbox_items (
        id VARCHAR(36) NOT NULL PRIMARY KEY, event_type VARCHAR(64) NOT NULL,
        aggregate_type VARCHAR(64) NOT NULL, aggregate_id VARCHAR(128) NOT NULL,
        payload_json TEXT NOT NULL, schema_version INTEGER DEFAULT 1 NOT NULL,
        status VARCHAR(16) DEFAULT 'pending' NOT NULL, attempts INTEGER DEFAULT 0 NOT NULL,
        next_retry_at DATETIME, idempotency_key VARCHAR(128) NOT NULL,
        last_error TEXT, remote_id VARCHAR(128), acked_at DATETIME, lease_until DATETIME,
        created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
        CONSTRAINT uq_outbox_items_idempotency_key UNIQUE (idempotency_key),
        CONSTRAINT ck_outbox_items_status CHECK (status in ('pending','sending','synced','failed','dead_letter')),
        CONSTRAINT ck_outbox_items_attempts CHECK (attempts >= 0),
        CONSTRAINT ck_outbox_items_schema_version CHECK (schema_version >= 1)
    )""",
    "CREATE INDEX ix_outbox_items_dispatch ON outbox_items (status, next_retry_at, created_at)",
    "CREATE INDEX ix_outbox_items_lease ON outbox_items (status, lease_until)",
    "CREATE INDEX ix_outbox_items_acked ON outbox_items (status, acked_at)",
    """CREATE TABLE diagnostic_events (
        id INTEGER NOT NULL PRIMARY KEY, event_uid VARCHAR(32) NOT NULL,
        fingerprint VARCHAR(20) NOT NULL, module VARCHAR(64) NOT NULL,
        operation VARCHAR(64) NOT NULL, category VARCHAR(32) NOT NULL,
        severity VARCHAR(16) NOT NULL, error_code VARCHAR(64) NOT NULL,
        request_id VARCHAR(64), duration_ms INTEGER, retry_count INTEGER NOT NULL,
        environment VARCHAR(40) NOT NULL, metadata_json TEXT NOT NULL, user_id INTEGER,
        occurrence_count INTEGER NOT NULL, first_seen_at DATETIME NOT NULL,
        last_seen_at DATETIME NOT NULL,
        CONSTRAINT uq_diagnostic_events_uid UNIQUE (event_uid)
    )""",
    "CREATE INDEX ix_diagnostic_events_time ON diagnostic_events (last_seen_at)",
    "CREATE INDEX ix_diagnostic_events_fingerprint ON diagnostic_events (fingerprint, last_seen_at)",
    """CREATE TABLE nonce_receipts (
        id INTEGER NOT NULL PRIMARY KEY, peer_id VARCHAR(128) NOT NULL,
        nonce VARCHAR(128) NOT NULL, received_at DATETIME NOT NULL, expires_at DATETIME NOT NULL,
        CONSTRAINT uq_nonce_receipts_peer_nonce UNIQUE (peer_id, nonce)
    )""",
    "CREATE INDEX ix_nonce_receipts_expiry ON nonce_receipts (expires_at)",
    """CREATE TABLE note_closures (
        id INTEGER NOT NULL PRIMARY KEY, request_uid VARCHAR(36) NOT NULL,
        service_note_id INTEGER NOT NULL, total_amount_cents INTEGER NOT NULL,
        paid_amount_cents INTEGER NOT NULL, outstanding_amount_cents INTEGER NOT NULL,
        closed_at DATETIME NOT NULL, closed_by INTEGER NOT NULL,
        CONSTRAINT uq_note_closures_note UNIQUE (service_note_id),
        CONSTRAINT uq_note_closures_request_uid UNIQUE (request_uid),
        CONSTRAINT ck_note_closures_request_uid CHECK (length(request_uid) = 36 and request_uid = lower(request_uid) and substr(request_uid, 9, 1) = '-' and substr(request_uid, 14, 1) = '-' and substr(request_uid, 19, 1) = '-' and substr(request_uid, 24, 1) = '-' and request_uid not glob '*[^0-9a-f-]*'),
        CONSTRAINT ck_note_closures_amounts CHECK (typeof(total_amount_cents) = 'integer' and total_amount_cents between 0 and 9223372036854775807 and typeof(paid_amount_cents) = 'integer' and paid_amount_cents between 0 and total_amount_cents and typeof(outstanding_amount_cents) = 'integer' and outstanding_amount_cents = total_amount_cents - paid_amount_cents),
        FOREIGN KEY(service_note_id) REFERENCES service_notes (id) ON DELETE RESTRICT,
        FOREIGN KEY(closed_by) REFERENCES users (id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE customer_receivables (
        id INTEGER NOT NULL PRIMARY KEY, customer_id INTEGER NOT NULL,
        source_note_id INTEGER NOT NULL, original_amount_cents INTEGER NOT NULL,
        remaining_amount_cents INTEGER NOT NULL, status VARCHAR(20) NOT NULL,
        created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
        CONSTRAINT uq_customer_receivables_source_note UNIQUE (source_note_id),
        CONSTRAINT ck_customer_receivables_status CHECK (status in ('OPEN','PARTIALLY_PAID','SETTLED')),
        CONSTRAINT ck_customer_receivables_money CHECK (original_amount_cents between 1 and 9223372036854775807 and remaining_amount_cents between 0 and original_amount_cents),
        FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE RESTRICT,
        FOREIGN KEY(source_note_id) REFERENCES service_notes (id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_customer_receivables_customer_status ON customer_receivables (customer_id, status)",
    """CREATE TABLE note_receivable_links (
        id INTEGER NOT NULL PRIMARY KEY, note_id INTEGER NOT NULL,
        receivable_id INTEGER NOT NULL, amount_snapshot_cents INTEGER NOT NULL,
        created_at DATETIME NOT NULL, created_by INTEGER NOT NULL,
        CONSTRAINT uq_note_receivable_links_pair UNIQUE (note_id, receivable_id),
        CONSTRAINT ck_note_receivable_links_amount CHECK (amount_snapshot_cents between 1 and 9223372036854775807),
        FOREIGN KEY(note_id) REFERENCES service_notes (id) ON DELETE RESTRICT,
        FOREIGN KEY(receivable_id) REFERENCES customer_receivables (id) ON DELETE RESTRICT,
        FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_note_receivable_links_receivable ON note_receivable_links (receivable_id)",
    """CREATE TABLE payment_allocations (
        id INTEGER NOT NULL PRIMARY KEY, payment_id INTEGER NOT NULL,
        receivable_id INTEGER, amount_cents INTEGER NOT NULL, created_at DATETIME NOT NULL,
        CONSTRAINT uq_payment_allocations_target UNIQUE (payment_id, receivable_id),
        CONSTRAINT ck_payment_allocations_amount CHECK (amount_cents between 1 and 9223372036854775807),
        FOREIGN KEY(payment_id) REFERENCES payments (id) ON DELETE RESTRICT,
        FOREIGN KEY(receivable_id) REFERENCES customer_receivables (id) ON DELETE RESTRICT
    )""",
    "CREATE UNIQUE INDEX uq_payment_allocations_current_note ON payment_allocations (payment_id) WHERE receivable_id IS NULL",
    "CREATE INDEX ix_payment_allocations_receivable ON payment_allocations (receivable_id)",
    """CREATE TABLE receivable_payments (
        id INTEGER NOT NULL PRIMARY KEY, request_uid VARCHAR(36) NOT NULL,
        receivable_id INTEGER NOT NULL, payment_method_id INTEGER NOT NULL,
        method_name_snapshot VARCHAR(80) NOT NULL, amount_cents INTEGER NOT NULL,
        paid_at DATETIME NOT NULL, created_by INTEGER NOT NULL, created_at DATETIME NOT NULL,
        CONSTRAINT uq_receivable_payments_request_uid UNIQUE (request_uid),
        CONSTRAINT ck_receivable_payments_request_uid CHECK (length(request_uid) = 36 and request_uid = lower(request_uid) and substr(request_uid, 9, 1) = '-' and substr(request_uid, 14, 1) = '-' and substr(request_uid, 19, 1) = '-' and substr(request_uid, 24, 1) = '-' and request_uid not glob '*[^0-9a-f-]*'),
        CONSTRAINT ck_receivable_payments_amount CHECK (amount_cents between 1 and 9223372036854775807),
        FOREIGN KEY(receivable_id) REFERENCES customer_receivables (id) ON DELETE RESTRICT,
        FOREIGN KEY(payment_method_id) REFERENCES cash_payment_methods (id) ON DELETE RESTRICT,
        FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_receivable_payments_receivable_paid ON receivable_payments (receivable_id, paid_at)",
    "CREATE INDEX ix_receivable_payments_method_paid ON receivable_payments (payment_method_id, paid_at)",
    "CREATE INDEX ix_payments_note_status_paid ON payments (service_note_id, status, paid_at)",
)


# Correção funcional de UX e recuperação administrativa. Todas as mudanças
# são aditivas e preservam integralmente pagamentos, cadeado e usuários.
FUNCTIONAL_UX_RECOVERY_0014_STATEMENTS = (
    "ALTER TABLE payments ADD COLUMN notes VARCHAR(1000) "
    "CONSTRAINT ck_payments_notes CHECK (notes is null or length(notes) <= 1000)",
    "ALTER TABLE admin_locks ADD COLUMN failed_attempt_count INTEGER NOT NULL DEFAULT 0 "
    "CONSTRAINT ck_admin_locks_failed_attempt_count CHECK (failed_attempt_count between 0 and 1000000)",
    "ALTER TABLE admin_locks ADD COLUMN lockout_until DATETIME",
    "ALTER TABLE admin_locks ADD COLUMN recovery_failed_attempt_count INTEGER NOT NULL DEFAULT 0 "
    "CONSTRAINT ck_admin_locks_recovery_failed_attempt_count CHECK (recovery_failed_attempt_count between 0 and 1000000)",
    "ALTER TABLE admin_locks ADD COLUMN recovery_lockout_until DATETIME",
    "ALTER TABLE admin_locks ADD COLUMN last_recovery_at DATETIME",
    """CREATE TABLE admin_recovery_codes (
        id INTEGER NOT NULL PRIMARY KEY,
        batch_uid VARCHAR(36) NOT NULL,
        code_hash VARCHAR(255) NOT NULL,
        status VARCHAR(12) NOT NULL,
        created_at DATETIME NOT NULL,
        created_by INTEGER NOT NULL,
        used_at DATETIME,
        used_by INTEGER,
        invalidated_at DATETIME,
        CONSTRAINT uq_admin_recovery_codes_hash UNIQUE (code_hash),
        CONSTRAINT ck_admin_recovery_codes_batch_uid CHECK (length(batch_uid) = 36 and batch_uid = lower(batch_uid)),
        CONSTRAINT ck_admin_recovery_codes_status CHECK (status in ('ACTIVE','USED','REVOKED')),
        CONSTRAINT ck_admin_recovery_codes_lifecycle CHECK (
            (status = 'ACTIVE' and used_at is null and used_by is null and invalidated_at is null)
            or (status = 'USED' and used_at is not null and used_by is not null)
            or (status = 'REVOKED' and invalidated_at is not null)
        ),
        FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT,
        FOREIGN KEY(used_by) REFERENCES users (id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_admin_recovery_codes_batch_status ON admin_recovery_codes (batch_uid, status)",
    "CREATE INDEX ix_admin_recovery_codes_status ON admin_recovery_codes (status)",
)


# Sessão persistente opcional para o login normal. O token nunca é armazenado:
# somente o SHA-256 de um segredo aleatório de alta entropia fica no SQLite.
# A tabela de códigos de recuperação permanece apenas por compatibilidade; a
# migration revoga todos os códigos ativos sem apagar histórico.
REMEMBER_SESSIONS_0015_STATEMENTS = (
    """CREATE TABLE remember_sessions (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        user_id INTEGER NOT NULL,
        token_hash VARCHAR(64) NOT NULL,
        installation_id VARCHAR(80) NOT NULL,
        auth_version INTEGER NOT NULL,
        session_generation_hash VARCHAR(64) NOT NULL,
        status VARCHAR(10) NOT NULL,
        created_at DATETIME NOT NULL,
        expires_at DATETIME NOT NULL,
        last_used_at DATETIME NOT NULL,
        rotated_at DATETIME NOT NULL,
        revoked_at DATETIME,
        CONSTRAINT uq_remember_sessions_token_hash UNIQUE (token_hash),
        CONSTRAINT ck_remember_sessions_id CHECK (
            length(id) = 32 and id = lower(id) and id not glob '*[^0-9a-f]*'
        ),
        CONSTRAINT ck_remember_sessions_token_hash CHECK (
            length(token_hash) = 64 and token_hash = lower(token_hash)
            and token_hash not glob '*[^0-9a-f]*'
        ),
        CONSTRAINT ck_remember_sessions_generation_hash CHECK (
            length(session_generation_hash) = 64
            and session_generation_hash = lower(session_generation_hash)
            and session_generation_hash not glob '*[^0-9a-f]*'
        ),
        CONSTRAINT ck_remember_sessions_auth_version CHECK (
            typeof(auth_version) = 'integer' and auth_version >= 1
        ),
        CONSTRAINT ck_remember_sessions_status CHECK (
            status in ('ACTIVE','REVOKED','EXPIRED')
        ),
        CONSTRAINT ck_remember_sessions_lifecycle CHECK (
            (status = 'ACTIVE' and revoked_at is null)
            or (status in ('REVOKED','EXPIRED') and revoked_at is not null)
        ),
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_remember_sessions_user_status_expiry "
    "ON remember_sessions (user_id, status, expires_at)",
    "CREATE INDEX ix_remember_sessions_status_expiry "
    "ON remember_sessions (status, expires_at)",
)
