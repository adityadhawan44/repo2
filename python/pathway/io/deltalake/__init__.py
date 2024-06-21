# Copyright © 2024 Pathway

from __future__ import annotations

from typing import Any

from pathway.internals import api, datasink, datasource
from pathway.internals._io_helpers import _format_output_value_fields
from pathway.internals.config import _check_entitlements
from pathway.internals.runtime_type_check import check_arg_types
from pathway.internals.schema import Schema
from pathway.internals.table import Table
from pathway.internals.table_io import table_from_datasource
from pathway.internals.trace import trace_user_frame
from pathway.io._utils import internal_connector_mode, read_schema
from pathway.io.s3 import AwsS3Settings

S3_URI_PREFIX = "s3://"


@check_arg_types
@trace_user_frame
def read(
    uri: str,
    schema: type[Schema],
    *,
    mode: str = "streaming",
    autocommit_duration_ms: int | None = 1500,
    persistent_id: str | None = None,
    debug_data: Any = None,
) -> Table:
    """
    Reads an **append-only** table from Delta Lake. Currently, only local lakes are
    supported, S3 support will be added soon.

    Args:
        uri: URI of the Delta Lake source that must be read.
        schema: Schema of the resulting table.
        mode: Denotes how the engine polls the new data from the source. Currently
            ``"streaming"`` and ``"static"`` are supported. If set to ``"streaming"``
            the engine will wait for the updates in the specified lake. It will track
            new row additions and reflect these events in the state. On the other hand,
            the ``"static"`` mode will only consider the available data and ingest all
            of it in one commit. The default value is ``"streaming"``.
        persistent_id: (unstable) An identifier, under which the state of the table
            will be persisted or ``None``, if there is no need to persist the state of this table.
            When a program restarts, it restores the state for all input tables according to what
            was saved for their ``persistent_id``. This way it's possible to configure the start of
            computations from the moment they were terminated last time.
        autocommit_duration_ms: The maximum time between two commits. Every
            ``autocommit_duration_ms`` milliseconds, the updates received by the connector are
            committed and pushed into Pathway's computation graph.
        debug_data: Static data replacing original one when debug mode is active.

    Examples:

    Consider an example with a stream of changes on a simple key-value table, streamed by
    another Pathway program with ``pw.io.deltalake.write`` method.

    To set the stage you may need to clear the existing lake object. Since it's a directory
    in the file system if can be done this way:

    >>> import shutil
    >>> shutil.rmtree("./local-lake", ignore_errors=True)

    Now you can start wrinting Pathway code. First, the schema of the table needs to be created:

    >>> import pathway as pw
    >>> class KVSchema(pw.Schema):
    ...     key: str
    ...     value: str

    Then, this table must be written into a Delta Lake storage. In the example, it can
    be created from the static data with ``pw.debug.table_from_markdown`` method and
    saved into the locally located lake:

    >>> output_table = pw.debug.table_from_markdown("key value \\n one Hello \\n two World")
    >>> pw.io.deltalake.write(output_table, "./local-lake")

    Now the producer code can be run with with a simple ``pw.run``:

    >>> pw.run(monitoring_level=pw.MonitoringLevel.NONE)

    After that, you can read this table with Pathway as well. It requires the specification
    of the URI and the schema that was created above. In addition, you can use the ``"static"``
    mode, so that the program finishes after the data is read:

    >>> input_table = pw.io.deltalake.read("./local-lake", KVSchema, mode="static")

    Please note that the table doesn't necessary have to be created by Pathway: an
    append-only Delta Table created in any other way will also be processed correctly.

    Finally, you can check that the resulting table contains the same set of rows by
    displaying it with ``pw.debug.compute_and_print``:

    >>> pw.debug.compute_and_print(input_table, include_id=False)
    key | value
    one | Hello
    two | World
    """
    _check_entitlements("deltalake")

    schema, api_schema = read_schema(
        schema=schema,
        value_columns=None,
        primary_key=None,
        types=None,
        default_values=None,
    )

    data_storage = api.DataStorage(
        storage_type="deltalake",
        path=uri,
        mode=internal_connector_mode(mode),
        persistent_id=persistent_id,
    )
    data_format = api.DataFormat(
        format_type="transparent",
        **api_schema,
    )

    data_source_options = datasource.DataSourceOptions(
        commit_duration_ms=autocommit_duration_ms
    )
    return table_from_datasource(
        datasource.GenericDataSource(
            datastorage=data_storage,
            dataformat=data_format,
            schema=schema,
            data_source_options=data_source_options,
            datasource_name="deltalake",
            append_only=True,
        ),
        debug_datasource=datasource.debug_datasource(debug_data),
    )


@check_arg_types
@trace_user_frame
def write(
    table: Table,
    uri: str,
    *,
    s3_connection_settings: AwsS3Settings | None = None,
    min_commit_frequency: int | None = 60_000,
) -> None:
    """
    Writes the stream of changes from ``table`` into `Delta Lake <https://delta.io/>_` data
    storage at the location specified by ``uri``. Supported storage types are S3 and the
    local filesystem.

    The storage type is determined by the URI: paths starting with ``s3://`` or ``s3a://``
    are for S3 storage, while all other paths use the filesystem.

    If the specified storage location doesn't exist, it will be created. The schema of
    the new table is inferred from the ``table``'s schema. The output table must include
    two additional integer columns: ``time``, representing the computation minibatch,
    and ``diff``, indicating the type of change (``1`` for row addition and ``-1`` for row deletion).

    Args:
        table: Table to be written.
        uri: URI of the target Delta Lake.
        s3_connection_settings: Configuration for S3 credentials when using S3 storage.
            In addition to the access key and secret access key, you can specify a custom
            endpoint, which is necessary for buckets hosted outside of Amazon AWS. If the
            custom endpoint is left blank, the authorized user's credentials for S3 will
            be used.
        min_commit_frequency: Specifies the minimum time interval between two data commits in
            storage, measured in milliseconds. If set to None, finalized minibatches will
            be committed as soon as possible. Keep in mind that each commit in Delta Lake
            creates a new file and writes an entry in the transaction log. Therefore, it
            is advisable to limit the frequency of commits to reduce the overhead of
            processing the resulting table. Note that to further optimize performance and
            reduce the number of chunks in the table, you can use \
`vacuum <https://docs.delta.io/latest/delta-utility.html#remove-files-no-longer-referenced-by-a-delta-table>`_
            or \
`optimize <https://docs.delta.io/2.0.2/optimizations-oss.html#optimize-performance-with-file-management>`_
            operations afterwards.

    Returns:
        None

    Example:

    Consider a table ``access_log`` that needs to be output to a Delta Lake storage
    located locally at the folder ``./logs/access-log``. It can be done as follows:

    >>> pw.io.deltalake.write(access_log, "./logs/access-log")  # doctest: +SKIP

    Please note that if there is no filesystem object at this path, the corresponding
    folder will be created. However, if you run this code twice, the new data will be
    appended to the storage created during the first run.

    It is also possible to save the table to S3 storage. To save the table to the
    ``access-log`` path within the ``logs`` bucket in the ``eu-west-3`` region,
    modify the code as follows:

    >>> pw.io.deltalake.write(  # doctest: +SKIP
    ...     access_log,
    ...     "s3://logs/access-log/",
    ...     s3_connection_settings=pw.io.s3.AwsS3Settings(
    ...         bucket_name="logs",
    ...         region="eu-west-3",
    ...         access_key=os.environ["S3_ACCESS_KEY"],
    ...         secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
    ...     )
    ... )

    Note that it is not necessary to specify the credentials explicitly if you are
    logged into S3. Pathway can deduce them for you. For an authorized user, the code
    can be simplified as follows:

    >>> pw.io.deltalake.write(access_log, "s3://logs/access-log/")  # doctest: +SKIP
    """

    _check_entitlements("deltalake")
    storage_options = {}
    if uri.startswith(S3_URI_PREFIX):
        if s3_connection_settings is None:
            s3_connection_settings = AwsS3Settings.new_from_path(uri)
        storage_options = s3_connection_settings.as_deltalake_storage_options()

    data_storage = api.DataStorage(
        storage_type="deltalake",
        path=uri,
        storage_options=storage_options,
        min_commit_frequency=min_commit_frequency,
    )
    data_format = api.DataFormat(
        format_type="identity",
        key_field_names=None,
        value_fields=_format_output_value_fields(table),
    )

    table.to(
        datasink.GenericDataSink(
            data_storage,
            data_format,
            datasink_name="deltalake",
        )
    )
