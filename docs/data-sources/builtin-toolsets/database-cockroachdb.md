# CockroachDB

Connect HolmesGPT to CockroachDB databases to analyze distributed query performance, investigate range distribution issues, check node health, examine cluster status, and read data for troubleshooting.

You can configure multiple CockroachDB instances with different names (e.g., `crdb-production`, `crdb-analytics`, `crdb-staging`).

## Creating a Read-Only User

```sql
-- Create user
CREATE USER holmes_readonly WITH PASSWORD 'your_secure_password';

-- Grant connection
GRANT CONNECT ON DATABASE your_database TO holmes_readonly;

-- Grant schema access
GRANT USAGE ON SCHEMA public TO holmes_readonly;

-- Grant read access to tables
GRANT SELECT ON ALL TABLES IN SCHEMA public TO holmes_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO holmes_readonly;

-- Grant access to system tables for performance analysis
GRANT SELECT ON crdb_internal.* TO holmes_readonly;
```

## Configuration

=== "Holmes CLI"

    **~/.holmes/config.yaml:**

    ```yaml
    toolsets:
      crdb-production:
        type: database
        config:
          connection_url: "postgresql://holmes_readonly:your_secure_password@cockroachdb.example.com:26257/defaultdb?sslmode=require"
        llm_instructions: "Production CockroachDB cluster with multi-region data"

      crdb-analytics:
        type: database
        config:
          connection_url: "postgresql://analyst:pass@analytics-crdb.internal:26257/analytics?sslmode=require"
        llm_instructions: "Analytics cluster for cross-region reporting"
    ```

    **Using environment variables:**

    ```yaml
    toolsets:
      crdb-production:
        type: database
        config:
          connection_url: "{{ env.COCKROACHDB_URL }}"
    ```

    **Connection URL format:**
    ```
    postgresql://[username]:[password]@[host]:[port]/[database]?sslmode=require
    ```

    Note: CockroachDB uses PostgreSQL wire protocol.

=== "Holmes Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic cockroachdb-credentials \
      --from-literal=url='postgresql://holmes_readonly:your_secure_password@cockroachdb.example.com:26257/defaultdb?sslmode=require' \
      -n holmes
    ```

    **Step 2: Configure in values.yaml**

    ```yaml
    additionalEnvVars:
      - name: COCKROACHDB_URL
        valueFrom:
          secretKeyRef:
            name: cockroachdb-credentials
            key: url

    toolsets:
      crdb-production:
        type: database
        config:
          connection_url: "{{ env.COCKROACHDB_URL }}"
        llm_instructions: "Production CockroachDB cluster with multi-region data"
    ```

    **Multiple instances:**

    ```yaml
    additionalEnvVars:
      - name: PROD_COCKROACHDB_URL
        valueFrom:
          secretKeyRef:
            name: cockroachdb-prod
            key: url
      - name: ANALYTICS_COCKROACHDB_URL
        valueFrom:
          secretKeyRef:
            name: cockroachdb-analytics
            key: url

    toolsets:
      crdb-production:
        type: database
        config:
          connection_url: "{{ env.PROD_COCKROACHDB_URL }}"

      crdb-analytics:
        type: database
        config:
          connection_url: "{{ env.ANALYTICS_COCKROACHDB_URL }}"
    ```

=== "Robusta Helm Chart"

    **Step 1: Create secret with credentials**

    ```bash
    kubectl create secret generic cockroachdb-credentials \
      --from-literal=url='postgresql://holmes_readonly:your_secure_password@cockroachdb.example.com:26257/defaultdb?sslmode=require' \
      -n default
    ```

    **Step 2: Configure in values.yaml**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: COCKROACHDB_URL
          valueFrom:
            secretKeyRef:
              name: cockroachdb-credentials
              key: url

      toolsets:
        crdb-production:
          type: database
          config:
            connection_url: "{{ env.COCKROACHDB_URL }}"
          llm_instructions: "Production CockroachDB cluster with multi-region data"
    ```

    **Multiple instances:**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: PROD_COCKROACHDB_URL
          valueFrom:
            secretKeyRef:
              name: cockroachdb-prod
              key: url
        - name: ANALYTICS_COCKROACHDB_URL
          valueFrom:
            secretKeyRef:
              name: cockroachdb-analytics
              key: url

      toolsets:
        crdb-production:
          type: database
          config:
            connection_url: "{{ env.PROD_COCKROACHDB_URL }}"

        crdb-analytics:
          type: database
          config:
            connection_url: "{{ env.ANALYTICS_COCKROACHDB_URL }}"
    ```

## Configuration Options

- **connection_url** (required): CockroachDB connection URL
- **read_only** (default: `true`): Only allow SELECT/SHOW/DESCRIBE/EXPLAIN/WITH statements
- **verify_ssl** (default: `true`): Verify SSL certificates
- **max_rows** (default: `200`): Maximum rows to return (1-10000)
- **llm_instructions**: Context about this database

## Common Use Cases

```
"Show cluster node status and range distribution"
```

```
"Analyze query: SELECT * FROM orders WHERE region = 'us-east'"
```

```
"Check for hot ranges causing contention"
```
