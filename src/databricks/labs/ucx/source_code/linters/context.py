from typing import cast

from databricks.sdk.service.workspace import Language

from databricks.labs.ucx.hive_metastore.table_migration_status import TableMigrationIndex
from databricks.labs.ucx.source_code.base import (
    CurrentSessionState,
    TableSqlCollector,
    TableCollector,
    DfsaCollector,
    DfsaSqlCollector,
)
from databricks.labs.ucx.source_code.linters.base import (
    Linter,
    SqlLinter,
    Fixer,
    SqlSequentialLinter,
    PythonLinter,
    DfsaPyCollector,
    TablePyCollector,
)
from databricks.labs.ucx.source_code.linters.python import (
    PythonSequentialLinter,
)
from databricks.labs.ucx.source_code.linters.directfs import DirectFsAccessPyLinter, DirectFsAccessSqlLinter
from databricks.labs.ucx.source_code.linters.imports import DbutilsPyLinter

from databricks.labs.ucx.source_code.linters.pyspark import (
    DirectFsAccessSqlPylinter,
    FromTableSqlPyLinter,
    SparkTableNamePyLinter,
    SparkSqlTablePyCollector,
)
from databricks.labs.ucx.source_code.linters.spark_connect import SparkConnectPyLinter
from databricks.labs.ucx.source_code.linters.table_creation import DBRv8d0PyLinter
from databricks.labs.ucx.source_code.linters.from_table import FromTableSqlLinter


class LinterContext:
    """The context with UCX's linters.

    Use this context outside the `linters` module as an entrypoint to the
    linters in the `linters' module.
    """

    def __init__(
        self,
        index: TableMigrationIndex | None = None,
        session_state: CurrentSessionState | None = None,
    ):
        self._index = index
        self.session_state = CurrentSessionState() if not session_state else session_state

        python_linters: list[PythonLinter] = []
        python_fixers: list[Fixer] = []
        python_dfsa_collectors: list[DfsaPyCollector] = []
        python_table_collectors: list[TablePyCollector] = []

        sql_linters: list[SqlLinter] = []
        sql_fixers: list[Fixer] = []
        sql_dfsa_collectors: list[DfsaSqlCollector] = []
        sql_table_collectors: list[TableSqlCollector] = []

        if self._index is not None:
            from_table = FromTableSqlLinter(self._index, session_state=self.session_state)
            sql_linters.append(from_table)
            sql_fixers.append(from_table)
            sql_table_collectors.append(from_table)
            spark_sql = FromTableSqlPyLinter(from_table)
            python_linters.append(spark_sql)
            python_fixers.append(spark_sql)
            python_table_collectors.append(SparkSqlTablePyCollector(from_table))
            spark_table = SparkTableNamePyLinter(from_table, self._index, self.session_state)
            python_linters.append(spark_table)
            python_fixers.append(spark_table)
            python_table_collectors.append(spark_table)

        sql_direct_fs = DirectFsAccessSqlLinter()
        sql_linters.append(sql_direct_fs)
        sql_dfsa_collectors.append(sql_direct_fs)

        python_linters += [
            DirectFsAccessPyLinter(self.session_state),
            DBRv8d0PyLinter(dbr_version=self.session_state.dbr_version),
            SparkConnectPyLinter(self.session_state),
            DbutilsPyLinter(self.session_state),
            DirectFsAccessSqlPylinter(sql_direct_fs),
        ]

        python_dfsa_collectors += [DirectFsAccessPyLinter(self.session_state, prevent_spark_duplicates=False)]

        self._linters: dict[Language, list[SqlLinter] | list[PythonLinter]] = {
            Language.PYTHON: python_linters,
            Language.SQL: sql_linters,
        }
        self._fixers: dict[Language, list[Fixer]] = {
            Language.PYTHON: python_fixers,
            Language.SQL: sql_fixers,
        }

        self._dfsa_collectors: dict[Language, list[DfsaSqlCollector] | list[DfsaPyCollector]] = {
            Language.PYTHON: python_dfsa_collectors,
            Language.SQL: sql_dfsa_collectors,
        }

        self._table_collectors: dict[Language, list[TableSqlCollector] | list[TablePyCollector]] = {
            Language.PYTHON: python_table_collectors,
            Language.SQL: sql_table_collectors,
        }

    def is_supported(self, language: Language) -> bool:
        return language in self._linters and language in self._fixers

    def linter(self, language: Language) -> Linter:
        if language not in self._linters:
            raise ValueError(f"Unsupported language: {language}")
        if language is Language.PYTHON:
            return PythonSequentialLinter(cast(list[PythonLinter], self._linters[language]), [], [])
        if language is Language.SQL:
            return SqlSequentialLinter(cast(list[SqlLinter], self._linters[language]), [], [])
        raise ValueError(f"Unsupported language: {language}")

    def fixer(self, language: Language, diagnostic_code: str) -> Fixer | None:
        """Get the fixer for a language that matches the code.

        The first fixer which name matches with the diagnostic code is returned. This logic assumes the fixers have
        unique names.

        Returns :
            Fixer | None : The fixer if a match is found, otherwise None.
        """
        for fixer in self._fixers.get(language, []):
            if fixer.is_supported(diagnostic_code):
                return fixer
        return None

    def dfsa_collector(self, language: Language) -> DfsaCollector:
        if language not in self._dfsa_collectors:
            raise ValueError(f"Unsupported language: {language}")
        if language is Language.PYTHON:
            return PythonSequentialLinter([], cast(list[DfsaPyCollector], self._dfsa_collectors[language]), [])
        if language is Language.SQL:
            return SqlSequentialLinter([], cast(list[DfsaSqlCollector], self._dfsa_collectors[language]), [])
        raise ValueError(f"Unsupported language: {language}")

    def tables_collector(self, language: Language) -> TableCollector:
        if language not in self._table_collectors:
            raise ValueError(f"Unsupported language: {language}")
        if language is Language.PYTHON:
            return PythonSequentialLinter([], [], cast(list[TablePyCollector], self._table_collectors[language]))
        if language is Language.SQL:
            return SqlSequentialLinter([], [], cast(list[TableSqlCollector], self._table_collectors[language]))
        raise ValueError(f"Unsupported language: {language}")

    def apply_fixes(self, language: Language, code: str) -> str:
        linter = self.linter(language)
        for advice in linter.lint(code):
            fixer = self.fixer(language, advice.code)
            if fixer:
                code = fixer.apply(code)
        return code
