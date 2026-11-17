-- Read-only schema fingerprint of the application tables in `public`.
-- One row per column, constraint, index and RLS flag, so two databases can be diffed line by line.
-- Grants and platform objects (Supabase roles, extensions) are deliberately excluded.
WITH app_tables AS (
    SELECT c.oid, c.relname, c.relrowsecurity
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
)
SELECT line FROM (
    SELECT format('column|%s|%s|%s|%s|%s', t.relname, a.attname, format_type(a.atttypid, a.atttypmod),
                  CASE WHEN a.attnotnull THEN 'not null' ELSE 'null' END,
                  coalesce(pg_get_expr(d.adbin, d.adrelid), '')) AS line
    FROM app_tables t
    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_attrdef d ON d.adrelid = t.oid AND d.adnum = a.attnum
    UNION ALL
    SELECT format('constraint|%s|%s|%s', t.relname, co.contype, pg_get_constraintdef(co.oid))
    FROM app_tables t JOIN pg_constraint co ON co.conrelid = t.oid
    UNION ALL
    SELECT format('index|%s|%s', t.relname, regexp_replace(pg_get_indexdef(i.indexrelid), '^CREATE (UNIQUE )?INDEX \S+ ', 'CREATE \1INDEX '))
    FROM app_tables t JOIN pg_index i ON i.indrelid = t.oid
    UNION ALL
    SELECT format('rls|%s|%s', t.relname, t.relrowsecurity) FROM app_tables t
) lines
ORDER BY line COLLATE "C";
