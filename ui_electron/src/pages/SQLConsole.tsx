import { useState, useEffect } from 'react';
import { endpoints } from '../api';

type ConnectionStatus = 'connected' | 'connecting' | 'disconnected';

export default function SQLConsole() {
    const [activeTab, setActiveTab] = useState<'query' | 'prompt'>('query');

    // Query Tab State
    const [query, setQuery] = useState('');
    const [results, setResults] = useState<any[]>([]);
    const [columns, setColumns] = useState<string[]>([]);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting');

    // Prompt Tab State
    const [copyFeedback, setCopyFeedback] = useState('');

    useEffect(() => {
        checkConnection();
    }, []);

    const checkConnection = async (attempt = 1) => {
        const maxAttempts = 3;
        setConnectionStatus('connecting');

        try {
            await endpoints.health();
            setConnectionStatus('connected');
        } catch (err) {
            if (attempt < maxAttempts) {
                // Wait 1 second before retrying
                setTimeout(() => checkConnection(attempt + 1), 1000);
            } else {
                setConnectionStatus('disconnected');
            }
        }
    };

    const executeQuery = async () => {
        if (!query.trim()) return;

        setLoading(true);
        setError('');
        setResults([]);
        setColumns([]);

        try {
            const res = await endpoints.sql.query(query);
            if (res.data.error) {
                setError(res.data.error);
            } else {
                setResults(res.data.data || []);
                setColumns(res.data.columns || []);
            }
        } catch (err: any) {
            setError(err.response?.data?.detail || 'Failed to execute query');
        } finally {
            setLoading(false);
        }
    };



    const LLM_PROMPT_TEXT = `I am working on a PostgreSQL database for a restaurant analytics system.
Below is the full schema of my database. Please use this schema context to answer all my future questions about writing SQL queries.

## Context
- The database stores restaurant orders from PetPooja (POS system).
- It tracks Customers, Menu Items, Orders, and Order Items.
- We have a specific focus on "Menu Clustering" (normalizing raw item names to clean menu items).

## Database Schema (\`schema.sql\`)
\`\`\`sql
-- ============================================================================
-- 1. RESTAURANTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS restaurants (
    restaurant_id SERIAL PRIMARY KEY,
    petpooja_restid VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    address TEXT,
    contact_information VARCHAR(50),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 2. CUSTOMERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS customers (
    customer_id SERIAL PRIMARY KEY,
    customer_identity_key VARCHAR(80) UNIQUE,
    name VARCHAR(255),
    name_normalized VARCHAR(255),
    phone VARCHAR(20),
    address TEXT,
    gstin VARCHAR(50),
    first_order_date TIMESTAMPTZ,
    last_order_date TIMESTAMPTZ,
    total_orders INTEGER DEFAULT 0,            
    total_spent DECIMAL(10,2) DEFAULT 0,      
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 3. MENU ITEMS
-- ============================================================================
CREATE TABLE IF NOT EXISTS menu_items (
    menu_item_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    total_revenue DECIMAL(12,2) DEFAULT 0,
    total_sold INTEGER DEFAULT 0,
    sold_as_item INTEGER DEFAULT 0,
    sold_as_addon INTEGER DEFAULT 0,
    is_verified BOOLEAN DEFAULT FALSE,
    suggestion_id UUID REFERENCES menu_items(menu_item_id),
    UNIQUE(name, type)
);

-- ============================================================================
-- 4. VARIANTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS variants (
    variant_id UUID PRIMARY KEY,
    variant_name TEXT NOT NULL UNIQUE,
    description TEXT,
    unit TEXT,
    value DECIMAL(10,2),
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 5. MENU ITEM VARIANTS (Mapping & Pricing)
-- ============================================================================
CREATE TABLE IF NOT EXISTS menu_item_variants (
    order_item_id VARCHAR(255) PRIMARY KEY,
    menu_item_id UUID NOT NULL REFERENCES menu_items(menu_item_id),
    variant_id UUID NOT NULL REFERENCES variants(variant_id),
    price DECIMAL(10,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    addon_eligible BOOLEAN DEFAULT FALSE,
    delivery_eligible BOOLEAN DEFAULT TRUE,
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 6. ORDERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS orders (
    order_id SERIAL PRIMARY KEY,
    petpooja_order_id INTEGER NOT NULL UNIQUE,
    stream_id INTEGER NOT NULL UNIQUE,
    event_id VARCHAR(255) NOT NULL UNIQUE,
    aggregate_id VARCHAR(100),
    customer_id INTEGER REFERENCES customers(customer_id),
    restaurant_id INTEGER REFERENCES restaurants(restaurant_id),
    occurred_at TIMESTAMPTZ NOT NULL,
    created_on TIMESTAMPTZ NOT NULL,
    order_type VARCHAR(50) NOT NULL,
    order_from VARCHAR(100) NOT NULL,
    sub_order_type VARCHAR(100),
    order_from_id VARCHAR(100),
    order_status VARCHAR(50) NOT NULL,
    biller VARCHAR(100),
    assignee VARCHAR(255),
    table_no VARCHAR(50),
    token_no VARCHAR(50),
    no_of_persons INTEGER DEFAULT 0,
    customer_invoice_id VARCHAR(100),
    core_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    tax_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    discount_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    delivery_charges DECIMAL(10,2) NOT NULL DEFAULT 0,
    packaging_charge DECIMAL(10,2) NOT NULL DEFAULT 0,
    service_charge DECIMAL(10,2) NOT NULL DEFAULT 0,
    round_off DECIMAL(10,2) DEFAULT 0,
    total DECIMAL(10,2) NOT NULL DEFAULT 0,
    comment TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 7. ORDER TAXES
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_taxes (
    order_tax_id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    tax_title VARCHAR(100) NOT NULL,
    tax_rate DECIMAL(5,2) NOT NULL,
    tax_type VARCHAR(10) NOT NULL,
    tax_amount DECIMAL(10,2) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 8. ORDER DISCOUNTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_discounts (
    order_discount_id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    discount_title VARCHAR(255) NOT NULL,
    discount_type VARCHAR(10) NOT NULL,
    discount_rate DECIMAL(5,2) DEFAULT 0,
    discount_amount DECIMAL(10,2) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 9. ORDER ITEMS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    menu_item_id UUID REFERENCES menu_items(menu_item_id),
    variant_id UUID REFERENCES variants(variant_id),
    petpooja_itemid BIGINT,
    itemcode VARCHAR(100),
    name_raw VARCHAR(500) NOT NULL,
    category_name VARCHAR(255),
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price DECIMAL(10,2) NOT NULL,
    total_price DECIMAL(10,2) NOT NULL,
    tax_amount DECIMAL(10,2) DEFAULT 0,
    discount_amount DECIMAL(10,2) DEFAULT 0,
    specialnotes TEXT,
    sap_code VARCHAR(100),
    vendoritemcode VARCHAR(100),
    match_confidence DECIMAL(5,2),
    match_method VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 10. ORDER ITEM ADDONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_item_addons (
    order_item_addon_id SERIAL PRIMARY KEY,
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id) ON DELETE CASCADE,
    menu_item_id UUID REFERENCES menu_items(menu_item_id),
    variant_id UUID REFERENCES variants(variant_id),
    petpooja_addonid VARCHAR(100),
    name_raw VARCHAR(255) NOT NULL,
    group_name VARCHAR(100),
    quantity INTEGER NOT NULL DEFAULT 1,
    price DECIMAL(10,2) NOT NULL,
    addon_sap_code VARCHAR(100),
    match_confidence DECIMAL(5,2),
    match_method VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
\`\`\`

## Views (\`views.sql\`)
\`\`\`sql
CREATE OR REPLACE VIEW menu_items_summary_view AS
SELECT 
    mi.menu_item_id,
    mi.name,
    mi.type,
    mi.total_revenue,
    mi.total_sold,
    mi.sold_as_item,
    mi.sold_as_addon,
    mi.is_active
FROM menu_items mi;
\`\`\`

When I ask for queries, please:
1. Prefer standard PostgreSQL syntax.
2. Consider that \`orders.created_on\` and \`occurred_at\` are TIMESTAMPTZ and should be used for time-based analysis (checking specifically for IST/Asia/Kolkata timezone if needed).
3. \`order_items\` and \`order_item_addons\` link to \`menu_items\` via \`menu_item_id\`.
`;

    const handleCopy = () => {
        navigator.clipboard.writeText(LLM_PROMPT_TEXT).then(() => {
            setCopyFeedback('Copied!');
            setTimeout(() => setCopyFeedback(''), 2000);
        });
    };

    return (
        <div style={{ padding: '20px', fontFamily: 'Inter, sans-serif' }}>
            {/* Header / Tabs */}
            <div style={{ display: 'flex', gap: '5px', marginBottom: '20px', background: 'white', padding: '5px', borderRadius: '30px', boxShadow: '0 2px 10px rgba(0,0,0,0.1)', maxWidth: '600px' }}>
                <button
                    onClick={() => setActiveTab('query')}
                    style={{
                        flex: 1,
                        padding: '12px',
                        background: activeTab === 'query' ? '#3B82F6' : 'transparent',
                        color: activeTab === 'query' ? 'white' : 'black',
                        border: 'none',
                        borderRadius: '25px',
                        cursor: 'pointer',
                        fontWeight: activeTab === 'query' ? 600 : 500,
                        transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                        boxShadow: activeTab === 'query' ? '0 2px 5px rgba(96, 165, 250, 0.4)' : 'none'
                    }}
                >
                    &gt;_ SQL Query
                </button>
                <button
                    onClick={() => setActiveTab('prompt')}
                    style={{
                        flex: 1,
                        padding: '12px',
                        background: activeTab === 'prompt' ? '#3B82F6' : 'transparent',
                        color: activeTab === 'prompt' ? 'white' : 'black',
                        border: 'none',
                        borderRadius: '25px',
                        cursor: 'pointer',
                        fontWeight: activeTab === 'prompt' ? 600 : 500,
                        transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                        boxShadow: activeTab === 'prompt' ? '0 2px 5px rgba(96, 165, 250, 0.4)' : 'none'
                    }}
                >
                    🤖 LLM Prompt
                </button>
            </div>

            {/* Content Area */}
            {activeTab === 'query' ? (
                // SQL Query View
                <>
                    <div className="card">
                        <textarea
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="Enter SQL query..."
                            rows={8}
                            style={{
                                width: '100%',
                                padding: '12px',
                                fontFamily: 'monospace',
                                fontSize: '14px',
                                border: '1px solid #ddd',
                                borderRadius: '8px',
                                backgroundColor: '#f5f5f5', // Light Grey
                                color: '#333',
                                resize: 'vertical'
                            }}
                        />
                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '10px' }}>
                            <button
                                onClick={executeQuery}
                                disabled={loading || !query.trim() || connectionStatus === 'disconnected'}
                                style={{
                                    padding: '10px 20px',
                                    background: connectionStatus === 'disconnected' ? '#666' : '#3B82F6',
                                    color: 'white',
                                    border: 'none',
                                    borderRadius: '8px',
                                    cursor: connectionStatus === 'disconnected' ? 'not-allowed' : 'pointer',
                                    opacity: (loading || !query.trim() || connectionStatus === 'disconnected') ? 0.5 : 1,
                                    fontWeight: 'bold',
                                    boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
                                }}
                            >
                                {loading ? 'Executing...' : 'Execute Query'}
                            </button>
                        </div>
                    </div>

                    {error && (
                        <div style={{
                            marginTop: '20px',
                            padding: '15px',
                            backgroundColor: '#fee2e2',
                            border: '1px solid #ef4444',
                            borderRadius: '8px',
                            color: '#b91c1c'
                        }}>
                            <strong>Error:</strong> {error}
                        </div>
                    )}

                    {results.length > 0 && (
                        <div className="card" style={{ marginTop: '20px', overflowX: 'auto', borderRadius: '12px', border: '1px solid #eee' }}>
                            <p style={{ marginBottom: '10px', color: '#666', fontSize: '0.9em' }}>
                                {results.length} row{results.length !== 1 ? 's' : ''} returned
                            </p>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9em' }}>
                                <thead>
                                    <tr style={{ background: '#f8f9fa', borderBottom: '2px solid #ddd' }}>
                                        {columns.map((col, idx) => (
                                            <th key={idx} style={{ padding: '12px', textAlign: 'left', fontWeight: '600', color: '#444' }}>
                                                {col}
                                            </th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {results.map((row, idx) => (
                                        <tr key={idx} style={{ borderBottom: '1px solid #eee' }}>
                                            {columns.map((col, colIdx) => (
                                                <td key={colIdx} style={{ padding: '10px', color: '#333' }}>
                                                    {row[col] !== null ? String(row[col]) : <span style={{ color: '#aaa', fontStyle: 'italic' }}>NULL</span>}
                                                </td>
                                            ))}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </>
            ) : (
                // LLM Prompt View
                <div className="card" style={{ position: 'relative' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
                        <h3 style={{ margin: 0, color: '#333' }}>Schema Context Prompt</h3>
                        <button
                            onClick={handleCopy}
                            style={{
                                padding: '4px 8px',
                                background: copyFeedback ? '#10B981' : '#E5E7EB', // Green if copied, otherwise Light Grey
                                color: copyFeedback ? 'white' : '#374151', // Dark grey text on light grey bg
                                border: '1px solid #D1D5DB', // Subtle border
                                borderRadius: '4px',
                                cursor: 'pointer',
                                fontWeight: '500',
                                transition: 'all 0.2s',
                                fontSize: '0.8em',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px'
                            }}
                        >
                            {copyFeedback || '📋 Copy'}
                        </button>
                    </div>

                    <pre style={{
                        background: '#f5f5f5', // Light Grey
                        padding: '20px',
                        borderRadius: '8px',
                        border: '1px solid #ddd',
                        overflowX: 'auto',
                        whiteSpace: 'pre-wrap',
                        fontFamily: 'monospace',
                        fontSize: '0.9em',
                        color: '#333',
                        maxHeight: '70vh',
                        overflowY: 'auto',
                        lineHeight: '1.5'
                    }}>
                        {LLM_PROMPT_TEXT}
                    </pre>
                </div>
            )}
        </div>
    );
}
