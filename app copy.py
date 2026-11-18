import streamlit as st
import psycopg2
import pandas as pd

# --------------------------------------------------
# PAGE CONFIG
# --------------------------------------------------

st.set_page_config(
    page_title="PostgreSQL Monitor",
    page_icon="🐘",
    layout="wide"
)

st.title("🐘 PostgreSQL Monitoring")
st.caption("CP_primary_2026")


# --------------------------------------------------
# DATABASE CONNECTION
# --------------------------------------------------

@st.cache_resource
def get_connection():
    return psycopg2.connect(
        host=st.secrets["postgres"]["host"],
        port=st.secrets["postgres"]["port"],
        database=st.secrets["postgres"]["database"],
        user=st.secrets["postgres"]["user"],
        password=st.secrets["postgres"]["password"]
    )


conn = get_connection()


# --------------------------------------------------
# HELPER FUNCTION
# --------------------------------------------------

def query_df(sql):
    return pd.read_sql_query(sql, conn)

def execute_query(sql):
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


# --------------------------------------------------
# DATABASE INFO
# --------------------------------------------------

db_info = query_df("""
SELECT
    current_database() AS database,
    pg_size_pretty(
        pg_database_size(current_database())
    ) AS database_size;
""")

database_name = db_info.iloc[0]["database"]
database_size = db_info.iloc[0]["database_size"]


# --------------------------------------------------
# CONNECTION INFO
# --------------------------------------------------

connection_info = query_df("""
SELECT
    count(*) AS total,
    count(*) FILTER (
        WHERE state = 'active'
    ) AS active,
    count(*) FILTER (
        WHERE state = 'idle'
    ) AS idle
FROM pg_stat_activity
WHERE datname = current_database();
""")


total_connections = int(connection_info.iloc[0]["total"])
active_connections = int(connection_info.iloc[0]["active"])
idle_connections = int(connection_info.iloc[0]["idle"])

# --------------------------------------------------
# CACHE HIT RATIO
# --------------------------------------------------

cache_info = query_df("""
SELECT
    round(
        100.0 * blks_hit /
        NULLIF(blks_hit + blks_read, 0),
        2
    ) AS cache_hit_ratio
FROM pg_stat_database
WHERE datname = current_database();
""")

cache_hit = cache_info.iloc[0]["cache_hit_ratio"]


# --------------------------------------------------
# TOP METRICS
# --------------------------------------------------

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "Database Size",
        database_size
    )

with col2:
    st.metric(
        "Connections",
        total_connections
    )

with col3:
    st.metric(
        "Active Queries",
        active_connections
    )

with col4:
    st.metric(
        "Cache Hit Ratio",
        f"{cache_hit}%"
    )


st.divider()


# --------------------------------------------------
# MEMORY & RESOURCE SETTINGS
# --------------------------------------------------

st.subheader("🧠 Memory & Resource Settings")
st.caption(
    "These are PostgreSQL configuration values, not live measurements of RAM in use. "
    "Actual use can grow with concurrent sessions and operations."
)

memory_settings = query_df("""
SELECT name, setting, unit, context
FROM pg_settings
WHERE name IN (
    'shared_buffers', 'work_mem', 'maintenance_work_mem',
    'autovacuum_work_mem', 'temp_buffers', 'effective_cache_size',
    'max_connections', 'huge_pages', 'block_size'
)
ORDER BY name;
""")

memory_explanations = {
    "shared_buffers": "PostgreSQL's shared cache for table and index pages; shared across connections.",
    "work_mem": "Allowance for each sort or hash operation in a query. A query may have several such operations, and concurrent queries multiply the potential use.",
    "maintenance_work_mem": "Memory available to maintenance operations such as VACUUM and CREATE INDEX. Concurrent operations can each use memory.",
    "autovacuum_work_mem": "Memory available to each autovacuum worker. A value of -1 means it uses maintenance_work_mem.",
    "temp_buffers": "Maximum buffer memory for temporary tables, allocated as needed per session.",
    "effective_cache_size": "Planner estimate of cache available from PostgreSQL and the operating system; it is not allocated by PostgreSQL.",
    "max_connections": "Maximum client connections. More sessions can increase overall memory demand, especially when running queries.",
    "huge_pages": "Whether PostgreSQL should use operating-system huge pages for its main shared memory area, where supported."
}

if not memory_settings.empty:
    settings_by_name = memory_settings.set_index("name")
    block_size_bytes = int(settings_by_name.loc["block_size", "setting"])
    memory_settings = memory_settings[memory_settings["name"] != "block_size"].copy()

    def format_bytes(byte_count):
        if byte_count >= 1024 ** 3:
            return f"{byte_count / (1024 ** 3):.2f} GB"
        if byte_count >= 1024 ** 2:
            return f"{byte_count / (1024 ** 2):.2f} MB"
        if byte_count >= 1024:
            return f"{byte_count / 1024:.2f} kB"
        return f"{byte_count} bytes"

    def format_setting_value(row):
        value = str(row["setting"])
        unit = row["unit"]
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return value if pd.isna(unit) else f"{value} {unit}".strip()

        # These settings are counts of PostgreSQL blocks, not kB values.
        if row["name"] in ("shared_buffers", "effective_cache_size", "temp_buffers"):
            return format_bytes(int(numeric_value) * block_size_bytes)

        # pg_settings reports memory settings in kB. Show larger sizes in MB/GB.
        if unit == "kB" and not pd.isna(unit):
            if numeric_value >= 1024 * 1024:
                return f"{numeric_value / (1024 * 1024):.2f} GB"
            if numeric_value >= 1024:
                return f"{numeric_value / 1024:.2f} MB"
            return f"{numeric_value:g} kB"

        return f"{value} {unit}".strip()

    memory_settings["value"] = memory_settings.apply(format_setting_value, axis=1)
    memory_settings["what it is for"] = memory_settings["name"].map(memory_explanations)
    st.dataframe(
        memory_settings[["name", "value", "context", "what it is for"]],
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("No memory settings were returned by pg_settings.")

st.info(
    "Work memory is a per-operation allowance, not a server-wide pool. One query can use it several times, "
    "and multiple queries can use it concurrently."
)

st.divider()


# --------------------------------------------------
# CONNECTION BREAKDOWN
# --------------------------------------------------

st.subheader("Connection Status")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Total", total_connections)

with col2:
    st.metric("Active", active_connections)

with col3:
    st.metric("Idle", idle_connections)


st.divider()

# --------------------------------------------------
# CONNECTION HISTORY
# --------------------------------------------------

st.divider()

st.subheader("📈 Connection History")

connection_history = query_df("""
SELECT
    recorded_at,
    total_connections,
    active_connections,
    idle_connections

FROM monitoring.database_metrics

WHERE database_name = current_database()
AND recorded_at >= now() - interval '24 hours'


ORDER BY recorded_at;
""")

if not connection_history.empty:

    connection_history["recorded_at"] = pd.to_datetime(
        connection_history["recorded_at"]
    )

    connection_history = connection_history.set_index(
        "recorded_at"
    )

    st.line_chart(
        connection_history[
            [
                "total_connections",
                "active_connections",
                "idle_connections"
            ]
        ]
    )


# --------------------------------------------------
# ACTIVE QUERIES
# --------------------------------------------------

st.subheader("🔥 Active Queries")

active_queries = query_df("""
SELECT
    pid,
    usename AS user,
    client_addr,
    state,
    now() - query_start AS duration,
    wait_event_type,
    wait_event,
    LEFT(query, 500) AS query
FROM pg_stat_activity
WHERE datname = current_database()

  AND state <> 'idle'
ORDER BY query_start;
""")

if active_queries.empty:
    st.success("No active queries")

else:
    st.dataframe(
        active_queries,
        use_container_width=True,
        hide_index=True
    )


st.divider()

# --------------------------------------------------
# READONLY USER ACTIVITY
# --------------------------------------------------

st.subheader("👤 readonly_user Activity")
st.caption("Current sessions and queries for readonly_user in this database.")

readonly_activity = query_df("""
SELECT
    pid,
    usename AS user,
    application_name,
    client_addr,
    state,
    backend_start,
    query_start,
    CASE
        WHEN query_start IS NOT NULL THEN now() - query_start
        ELSE NULL
    END AS query_duration,
    wait_event_type,
    wait_event,
    LEFT(query, 1000) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND usename = 'readonly_user'
ORDER BY query_start DESC NULLS LAST, backend_start DESC;
""")

if readonly_activity.empty:
    st.info("No readonly_user sessions found in this database.")
else:
    st.dataframe(
        readonly_activity,
        use_container_width=True,
        hide_index=True
    )


st.divider()

# --------------------------------------------------
# QUERY PANEL
# --------------------------------------------------

st.subheader("SQL Query")
st.caption("Run a read-only query and view its results. Results are limited to 10,000 rows.")

query_text = st.text_area(
    "Query",
    value="SELECT *\nFROM monitoring.database_metrics\nORDER BY recorded_at DESC\nLIMIT 100;",
    height=180,
    key="sql_query_input",
)

if st.button("Run query", type="primary", key="run_sql_query"):
    if not query_text.strip():
        st.warning("Enter a SQL query first.")
    else:
        try:
            # Clear any read transaction left open by the dashboard before
            # starting a transaction that PostgreSQL enforces as read-only.
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(query_text)

                if cur.description is None:
                    st.info("The query completed without returning rows.")
                else:
                    columns = [column.name for column in cur.description]
                    rows = cur.fetchmany(10001)
                    results = pd.DataFrame(rows[:10000], columns=columns)
                    st.caption(f"Showing {len(results):,} row(s).")
                    st.dataframe(results, use_container_width=True, hide_index=True)
                    if len(rows) > 10000:
                        st.info("Results were truncated at 10,000 rows.")
        except Exception as exc:
            st.error(f"Query failed: {exc}")
        finally:
            conn.rollback()


# --------------------------------------------------
# LONG RUNNING QUERIES
# --------------------------------------------------

st.subheader("🐌 Long Running Queries (> 30 minutes)")

long_queries = query_df("""
SELECT
    pid,
    usename AS user,
    client_addr,
    state,
    now() - query_start AS duration,
    wait_event_type,
    wait_event,
    LEFT(query, 1000) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND query_start IS NOT NULL
  AND now() - query_start > interval '30 minutes'
ORDER BY query_start;
""")

if long_queries.empty:
    st.success("No queries running longer than 30 minutes")

else:
    st.warning(
        f"{len(long_queries)} long-running query(s) detected"
    )

    st.dataframe(
        long_queries,
        use_container_width=True,
        hide_index=True
    )


st.divider()

# --------------------------------------------------
# BLOCKING / LOCKS
# --------------------------------------------------

st.divider()

st.subheader("🔒 Blocking & Locks")

blocking_queries = query_df("""
SELECT
    blocked.pid AS blocked_pid,
    blocked.usename AS blocked_user,
    now() - blocked.query_start AS blocked_duration,
    LEFT(blocked.query, 500) AS blocked_query,

    blocker.pid AS blocker_pid,
    blocker.usename AS blocker_user,
    now() - blocker.query_start AS blocker_duration,
    LEFT(blocker.query, 500) AS blocker_query,

    blocked.wait_event_type,
    blocked.wait_event

FROM pg_stat_activity blocked

JOIN LATERAL (
    SELECT
        unnest(pg_blocking_pids(blocked.pid)) AS pid
) blocking
ON true

JOIN pg_stat_activity blocker
    ON blocker.pid = blocking.pid

WHERE blocked.datname = current_database()
ORDER BY blocked.query_start;
""")

if blocking_queries.empty:

    st.success("✅ No blocked queries detected")

else:

    st.error(
        f"🚨 {len(blocking_queries)} blocked query(s) detected"
    )

    if not blocking_queries.empty:
        st.warning(
            "⚠️ Queries are currently being blocked. "
            "Check the blocker PID and blocker query below."
        )
    st.dataframe(
        blocking_queries,
        use_container_width=True,
        hide_index=True
    )

# --------------------------------------------------
# CURRENT LOCKS
# --------------------------------------------------

st.subheader("🔐 Current Locks")

locks = query_df("""
SELECT
    l.pid,
    a.usename AS user,
    a.state,

    l.locktype,
    l.mode,
    l.granted,

    l.relation::regclass AS relation,

    now() - a.query_start AS query_duration,

    LEFT(a.query, 500) AS query

FROM pg_locks l

LEFT JOIN pg_stat_activity a
    ON a.pid = l.pid

WHERE a.datname = current_database()

ORDER BY
    l.granted,
    a.query_start;
""")

if locks.empty:

    st.info("No locks found")

else:

    st.dataframe(
        locks,
        use_container_width=True,
        hide_index=True
    )

# --------------------------------------------------
# LARGEST TABLES
# --------------------------------------------------

st.divider()

st.subheader("📦 Largest Tables")

largest_tables = query_df("""
SELECT
    schemaname AS schema,
    relname AS table_name,

    pg_size_pretty(
        pg_total_relation_size(relid)
    ) AS total_size,

    pg_size_pretty(
        pg_relation_size(relid)
    ) AS table_size,

    pg_size_pretty(
        pg_indexes_size(relid)
    ) AS index_size,

    n_live_tup AS estimated_rows

FROM pg_stat_user_tables

ORDER BY pg_total_relation_size(relid) DESC

LIMIT 20;
""")

st.dataframe(
    largest_tables,
    use_container_width=True,
    hide_index=True
)

# --------------------------------------------------
# TABLE HEALTH
# --------------------------------------------------

st.subheader("🩺 Table Health")

table_health = query_df("""
SELECT

    schemaname AS schema,

    relname AS table_name,

    n_live_tup AS live_rows,

    n_dead_tup AS dead_rows,

    CASE
        WHEN n_live_tup > 0
        THEN ROUND(
            100.0 * n_dead_tup /
            (n_live_tup + n_dead_tup),
            2
        )
        ELSE 0
    END AS dead_tuple_pct,

    seq_scan,

    idx_scan,

    last_vacuum,

    last_autovacuum,

    last_analyze,

    last_autoanalyze

FROM pg_stat_user_tables

ORDER BY n_dead_tup DESC

LIMIT 50;
""")

st.dataframe(
    table_health,
    use_container_width=True,
    hide_index=True
)

# --------------------------------------------------
# TABLES REQUIRING ATTENTION
# --------------------------------------------------

problem_tables = table_health[
    (table_health["dead_tuple_pct"] >= 5)
    |
    (
        (table_health["seq_scan"] > table_health["idx_scan"])
        &
        (table_health["idx_scan"] > 0)
    )
]

if not problem_tables.empty:

    st.warning(
        f"⚠️ {len(problem_tables)} table(s) may require attention"
    )

    st.dataframe(
        problem_tables,
        use_container_width=True,
        hide_index=True
    )

else:

    st.success(
        "✅ No obvious table health issues detected"
    )

# --------------------------------------------------
# INDEX USAGE
# --------------------------------------------------

st.divider()

st.subheader("📇 Index Usage")

index_usage = query_df("""
SELECT
    schemaname AS schema,
    relname AS table_name,
    indexrelname AS index_name,

    pg_size_pretty(
        pg_relation_size(indexrelid)
    ) AS index_size,

    idx_scan AS scans,

    idx_tup_read AS tuples_read,

    idx_tup_fetch AS tuples_fetched

FROM pg_stat_user_indexes

ORDER BY pg_relation_size(indexrelid) DESC

LIMIT 50;
""")

st.dataframe(
    index_usage,
    use_container_width=True,
    hide_index=True
)

# --------------------------------------------------
# UNUSED INDEXES
# --------------------------------------------------

st.subheader("⚠️ Potentially Unused Indexes")

unused_indexes = query_df("""
SELECT
    schemaname AS schema,
    relname AS table_name,
    indexrelname AS index_name,

    pg_size_pretty(
        pg_relation_size(indexrelid)
    ) AS index_size,

    idx_scan AS scans

FROM pg_stat_user_indexes

WHERE idx_scan = 0

ORDER BY pg_relation_size(indexrelid) DESC

LIMIT 50;
""")

if unused_indexes.empty:

    st.success("✅ No indexes with zero scans")

else:

    st.warning(
        f"⚠️ {len(unused_indexes)} index(es) have zero scans"
    )

    st.dataframe(
        unused_indexes,
        use_container_width=True,
        hide_index=True
    )

# --------------------------------------------------
# INDEX RECOMMENDATIONS
# --------------------------------------------------

st.divider()

st.subheader("💡 Table-wise Index Recommendations")
st.caption(
    "Checks foreign keys for a matching leading index. This is a structural "
    "check; workload-specific indexes should be confirmed with slow-query "
    "plans (EXPLAIN ANALYZE)."
)

missing_fk_indexes = query_df("""
SELECT
    ns.nspname AS schema,
    tbl.relname AS table_name,
    con.conname AS foreign_key,
    string_agg(quote_ident(att.attname), ', ' ORDER BY key_col.ordinality)
        AS columns,
    format(
        'CREATE INDEX ON %I.%I (%s);',
        ns.nspname,
        tbl.relname,
        string_agg(quote_ident(att.attname), ', ' ORDER BY key_col.ordinality)
    ) AS suggested_sql
FROM pg_constraint con
JOIN pg_class tbl ON tbl.oid = con.conrelid
JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY
    AS key_col(attnum, ordinality)
JOIN pg_attribute att
    ON att.attrelid = con.conrelid
   AND att.attnum = key_col.attnum
WHERE con.contype = 'f'
  AND ns.nspname NOT IN ('pg_catalog', 'information_schema')
  AND NOT EXISTS (
      SELECT 1
      FROM pg_index idx
      WHERE idx.indrelid = con.conrelid
        AND idx.indisvalid
        AND idx.indnkeyatts >= cardinality(con.conkey)
        AND (
            SELECT array_agg(index_col.attnum::smallint ORDER BY index_col.ordinality)
            FROM unnest(idx.indkey) WITH ORDINALITY
                AS index_col(attnum, ordinality)
            WHERE index_col.ordinality <= cardinality(con.conkey)
        ) = con.conkey
  )
GROUP BY ns.nspname, tbl.relname, con.conname
ORDER BY ns.nspname, tbl.relname, con.conname;
""")

if missing_fk_indexes.empty:
    st.success("✅ All foreign keys have a matching leading index")
else:
    st.warning(
        f"⚠️ {len(missing_fk_indexes)} foreign key(s) have no matching leading index"
    )
    st.dataframe(
        missing_fk_indexes,
        use_container_width=True,
        hide_index=True
    )

# --------------------------------------------------
# SCAN STATISTICS
# --------------------------------------------------

st.subheader("🔎 Sequential vs Index Scans")

scan_stats = query_df("""
SELECT
    schemaname AS schema,
    relname AS table_name,

    seq_scan,
    idx_scan,

    seq_tup_read,
    idx_tup_fetch,

    n_live_tup

FROM pg_stat_user_tables

ORDER BY seq_tup_read DESC

LIMIT 50;
""")

st.dataframe(
    scan_stats,
    use_container_width=True,
    hide_index=True
)

# --------------------------------------------------
# DATABASE ACTIVITY
# --------------------------------------------------

st.divider()

st.subheader("📈 Database Activity")

activity = query_df("""
SELECT
    datname AS database,

    xact_commit AS commits,
    xact_rollback AS rollbacks,

    blks_read AS blocks_read,
    blks_hit AS blocks_hit,

    temp_files,
    pg_size_pretty(temp_bytes) AS temp_bytes,

    tup_returned,
    tup_fetched,
    tup_inserted,
    tup_updated,
    tup_deleted

FROM pg_stat_database

WHERE datname = current_database();
""")

st.dataframe(
    activity,
    use_container_width=True,
    hide_index=True
)

cache_ratio = query_df("""
SELECT
    round(
        100.0 * blks_hit /
        NULLIF(blks_hit + blks_read, 0),
        2
    ) AS cache_hit,

    blks_hit,
    blks_read

FROM pg_stat_database

WHERE datname = current_database();
""")

cache_hit_value = float(cache_ratio.iloc[0]["cache_hit"])

if cache_hit_value >= 99:

    st.success(
        f"🟢 Cache hit ratio: {cache_hit_value}%"
    )

elif cache_hit_value >= 95:

    st.warning(
        f"🟡 Cache hit ratio: {cache_hit_value}%"
    )

else:

    st.error(
        f"🔴 Cache hit ratio: {cache_hit_value}%"
    )


# --------------------------------------------------
# DATABASE ACTIVITY HISTORY
# --------------------------------------------------

st.subheader("📊 Database Activity History")

activity_history = query_df("""
SELECT
    recorded_at,

    xact_commit,
    xact_rollback,

    tup_inserted,
    tup_updated,
    tup_deleted

FROM monitoring.database_metrics

WHERE database_name = current_database()
AND recorded_at >= now() - interval '24 hours'

ORDER BY recorded_at;
""")

if not activity_history.empty:

    activity_history["recorded_at"] = pd.to_datetime(
        activity_history["recorded_at"]
    )

    activity_history = activity_history.set_index(
        "recorded_at"
    )

    st.line_chart(
        activity_history[
            [
                "tup_inserted",
                "tup_updated",
                "tup_deleted"
            ]
        ]
    )
# --------------------------------------------------
# REFRESH
# --------------------------------------------------

if st.button("🔄 Refresh"):
    st.rerun()
