import logging
import os
import re
from abc import ABC
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type
from urllib.parse import urlparse

from pydantic import ConfigDict, Field

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)

# SQL statements that are safe for read-only access
_READONLY_PATTERN = re.compile(
    r"^\s*(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN|WITH)\b",
    re.IGNORECASE,
)

# Statements that modify data or schema
_WRITE_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE|MERGE|GRANT|REVOKE|CALL|EXEC)\b",
    re.IGNORECASE,
)

# Maximum number of rows to return from a query
_MAX_ROWS = 200

# Map of common URL scheme prefixes to SQLAlchemy drivers that are pure-Python
# and bundled with holmesgpt (no C extensions needed).
_DRIVER_MAP: Dict[str, str] = {
    "postgresql": "postgresql+pg8000",
    "postgres": "postgresql+pg8000",
    "mysql": "mysql+pymysql",
    "mysql+mysqldb": "mysql+pymysql",
    "mariadb": "mysql+pymysql",
    "sqlite": "sqlite",
    "mssql": "mssql+pymssql",
}


def _normalise_url(raw_url: str) -> str:
    """Rewrite a connection URL to use a pure-Python driver when possible."""
    parsed = urlparse(raw_url)
    scheme = parsed.scheme  # e.g. "postgresql", "mysql+pymysql", "postgres"

    for prefix, replacement in _DRIVER_MAP.items():
        if scheme == prefix or scheme.startswith(prefix + "+"):
            # Only replace if the user hasn't already picked the right driver
            if scheme != replacement:
                return raw_url.replace(scheme, replacement, 1)
            return raw_url

    # Unknown scheme – return as-is and let SQLAlchemy handle it
    return raw_url


class DatabaseConfig(ToolsetConfig):
    """Configuration for the SQL database toolset.

    Example configuration:
    ```yaml
    connection_url: "postgresql://user:password@host:5432/mydb"
    ```
    """

    connection_url: str = Field(
        title="Connection URL",
        description=(
            "SQLAlchemy-compatible database connection URL. "
            "Supported databases: PostgreSQL, MySQL/MariaDB, SQLite, SQL Server. "
            "Pure-Python drivers are used automatically (pg8000, PyMySQL, pymssql)."
        ),
        examples=["{{ env.DATABASE_URL }}"],
    )


class DatabaseToolset(Toolset):
    """Toolset for querying SQL databases via SQLAlchemy.

    Provides read-only access to any SQLAlchemy-compatible database.
    Write operations (INSERT, UPDATE, DELETE, DROP, etc.) are blocked.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    config_classes: ClassVar[list[Type[DatabaseConfig]]] = [DatabaseConfig]

    def __init__(self):
        super().__init__(
            name="database/sql",
            enabled=False,
            description="Query SQL databases (PostgreSQL, MySQL, SQLite, SQL Server) with read-only access",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/database/",
            icon_url="https://www.postgresql.org/favicon.ico",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
        )
        self.tools = [
            DatabaseQuery(self),
            DatabaseListTables(self),
            DatabaseDescribeTable(self),
        ]
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.config = DatabaseConfig(**config)
            return self._perform_health_check()
        except Exception as e:
            return False, f"Failed to validate database configuration: {e}"

    def _perform_health_check(self) -> Tuple[bool, str]:
        try:
            import sqlalchemy

            url = _normalise_url(self.database_config.connection_url)
            engine = sqlalchemy.create_engine(url, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(sqlalchemy.text("SELECT 1"))
            dialect = engine.dialect.name
            engine.dispose()
            return True, f"Connected to {dialect} database"
        except Exception as e:
            return False, f"Database connection failed: {e}"

    @property
    def database_config(self) -> DatabaseConfig:
        return self.config  # type: ignore

    def execute_query(self, sql: str, limit: Optional[int] = None) -> Dict[str, Any]:
        """Execute a read-only SQL query and return results as a dict.

        Returns:
            Dict with keys: columns, rows, row_count, truncated
        """
        import sqlalchemy

        if _WRITE_PATTERN.match(sql):
            raise ValueError(
                f"Write operations are not allowed. "
                f"Only SELECT, SHOW, DESCRIBE, EXPLAIN, and WITH statements are permitted. "
                f"Received: {sql[:80]}"
            )

        if not _READONLY_PATTERN.match(sql):
            raise ValueError(
                f"Only SELECT, SHOW, DESCRIBE, EXPLAIN, and WITH statements are permitted. "
                f"Received: {sql[:80]}"
            )

        effective_limit = min(limit or _MAX_ROWS, _MAX_ROWS)
        url = _normalise_url(self.database_config.connection_url)
        engine = sqlalchemy.create_engine(url)
        try:
            with engine.connect() as conn:
                result = conn.execute(sqlalchemy.text(sql))
                columns = list(result.keys())
                rows: List[List[Any]] = []
                truncated = False
                for i, row in enumerate(result):
                    if i >= effective_limit:
                        truncated = True
                        break
                    rows.append([_serialize_value(v) for v in row])

                return {
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "truncated": truncated,
                }
        finally:
            engine.dispose()


def _serialize_value(val: Any) -> Any:
    """Convert database values to JSON-safe types."""
    if val is None:
        return None
    if isinstance(val, (int, float, bool, str)):
        return val
    if isinstance(val, bytes):
        return val.hex()
    if isinstance(val, (dict, list)):
        return val
    # datetime, Decimal, UUID, etc.
    return str(val)


class BaseDatabaseTool(Tool, ABC):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, toolset: DatabaseToolset, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._toolset = toolset


class DatabaseQuery(BaseDatabaseTool):
    """Execute a read-only SQL query against the connected database."""

    def __init__(self, toolset: DatabaseToolset):
        super().__init__(
            toolset=toolset,
            name="database_query",
            description=(
                "Execute a read-only SQL query. Only SELECT, SHOW, DESCRIBE, EXPLAIN, "
                "and WITH (CTE) statements are allowed. Returns up to 200 rows. "
                "Always use LIMIT to control result size."
            ),
            parameters={
                "sql": ToolParameter(
                    description=(
                        "The SQL query to execute. Must be a read-only statement "
                        "(SELECT, SHOW, DESCRIBE, EXPLAIN, WITH). "
                        "Use LIMIT to control result size. "
                        "Example: SELECT * FROM users WHERE created_at > '2024-01-01' LIMIT 50"
                    ),
                    type="string",
                    required=True,
                ),
                "limit": ToolParameter(
                    description="Maximum number of rows to return (default: 200, max: 200)",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        sql = params["sql"]
        limit = params.get("limit")

        try:
            data = self._toolset.execute_query(sql, limit=limit)
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )
        except Exception as e:
            error_msg = str(e)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Query failed: {error_msg}. SQL: {sql[:200]}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        sql = params.get("sql", "")
        short = sql[:60].replace("\n", " ")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: {short}"


class DatabaseListTables(BaseDatabaseTool):
    """List tables in the connected database."""

    def __init__(self, toolset: DatabaseToolset):
        super().__init__(
            toolset=toolset,
            name="database_list_tables",
            description=(
                "List all tables (and optionally views) in the database. "
                "Use schema parameter to filter by schema."
            ),
            parameters={
                "schema": ToolParameter(
                    description=(
                        "Schema to list tables from. Defaults to the database default schema "
                        "(e.g. 'public' for PostgreSQL)."
                    ),
                    type="string",
                    required=False,
                ),
                "include_views": ToolParameter(
                    description="Include views in the listing (default: true)",
                    type="boolean",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            import sqlalchemy

            schema = params.get("schema")
            include_views = params.get("include_views", True)

            url = _normalise_url(self._toolset.database_config.connection_url)
            engine = sqlalchemy.create_engine(url)
            try:
                inspector = sqlalchemy.inspect(engine)
                tables = inspector.get_table_names(schema=schema)
                result: Dict[str, Any] = {"tables": sorted(tables)}

                if include_views:
                    views = inspector.get_view_names(schema=schema)
                    result["views"] = sorted(views)

                result["total_count"] = len(tables) + (
                    len(views) if include_views else 0
                )
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=result,
                    params=params,
                )
            finally:
                engine.dispose()

        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to list tables: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        schema = params.get("schema", "default")
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: List tables in {schema}"
        )


class DatabaseDescribeTable(BaseDatabaseTool):
    """Describe the schema of a specific table."""

    def __init__(self, toolset: DatabaseToolset):
        super().__init__(
            toolset=toolset,
            name="database_describe_table",
            description=(
                "Get the column definitions and constraints for a table. "
                "Shows column names, types, nullability, defaults, primary keys, "
                "foreign keys, and indexes."
            ),
            parameters={
                "table_name": ToolParameter(
                    description="Name of the table to describe",
                    type="string",
                    required=True,
                ),
                "schema": ToolParameter(
                    description="Schema the table belongs to (optional)",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            import sqlalchemy

            table_name = params["table_name"]
            schema = params.get("schema")

            url = _normalise_url(self._toolset.database_config.connection_url)
            engine = sqlalchemy.create_engine(url)
            try:
                inspector = sqlalchemy.inspect(engine)

                columns = inspector.get_columns(table_name, schema=schema)
                pk = inspector.get_pk_constraint(table_name, schema=schema)
                fks = inspector.get_foreign_keys(table_name, schema=schema)
                indexes = inspector.get_indexes(table_name, schema=schema)

                col_info = []
                for col in columns:
                    col_info.append(
                        {
                            "name": col["name"],
                            "type": str(col["type"]),
                            "nullable": col.get("nullable", True),
                            "default": str(col["default"])
                            if col.get("default")
                            else None,
                        }
                    )

                fk_info = []
                for fk in fks:
                    fk_info.append(
                        {
                            "constrained_columns": fk.get("constrained_columns", []),
                            "referred_table": fk.get("referred_table"),
                            "referred_columns": fk.get("referred_columns", []),
                        }
                    )

                idx_info = []
                for idx in indexes:
                    idx_info.append(
                        {
                            "name": idx.get("name"),
                            "columns": idx.get("column_names", []),
                            "unique": idx.get("unique", False),
                        }
                    )

                result = {
                    "table_name": table_name,
                    "schema": schema,
                    "columns": col_info,
                    "primary_key": pk.get("constrained_columns", []) if pk else [],
                    "foreign_keys": fk_info,
                    "indexes": idx_info,
                }

                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=result,
                    params=params,
                )
            finally:
                engine.dispose()

        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to describe table '{params.get('table_name')}': {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        table = params.get("table_name", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Describe {table}"
