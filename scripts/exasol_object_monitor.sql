--/
CREATE OR REPLACE SCRIPT MONITOR_DB_OBJECT_ACCESS(
    INCLUDE_SYSTEM_SCHEMAS,
    INCLUDE_METADATA_ONLY_OBJECTS
)
RETURNS TABLE AS

    ---------------------------------------------------------------------------
    -- Purpose
    --   Production monitoring script for schema/object accessibility.
    --
    -- What it checks
    --   1. Schema can be opened.
    --   2. Schema object metadata can be enumerated.
    --   3. Every table/view/virtual table can be compiled with SELECT * WHERE 1=0.
    --   4. Column metadata can be read for every table/view.
    --   5. Scripts, functions, and connections are reported as metadata-only
    --      because executing them blindly would not be safe in production.
    --
    -- Parameters
    --   INCLUDE_SYSTEM_SCHEMAS:
    --       FALSE = skip SYS and EXA_% schemas, recommended default
    --       TRUE  = include system schemas
    --
    --   INCLUDE_METADATA_ONLY_OBJECTS:
    --       TRUE  = include scripts, functions, connections as metadata-only rows
    --       FALSE = only check schemas/tables/views
    ---------------------------------------------------------------------------

    local results = {}

    local function as_bool(v)
        if v == true then
            return true
        end

        if v == nil then
            return false
        end

        local s = string.upper(tostring(v))
        return s == 'TRUE' or s == '1' or s == 'Y' or s == 'YES'
    end

    local include_system_schemas = as_bool(INCLUDE_SYSTEM_SCHEMAS)

    local include_metadata_only_objects = true
    if INCLUDE_METADATA_ONLY_OBJECTS ~= nil then
        include_metadata_only_objects = as_bool(INCLUDE_METADATA_ONLY_OBJECTS)
    end

    local function str(v)
        if v == nil then
            return ''
        end
        return tostring(v)
    end

    local function qid(v)
        if v == nil then
            return ''
        end

        local s = tostring(v)
        s = string.gsub(s, '"', '""')
        return '"' .. s .. '"'
    end

    local function fq(schema_name, object_name)
        return qid(schema_name) .. '.' .. qid(object_name)
    end

    local function add_row(
        check_ts,
        database_name,
        monitor_user,
        schema_name,
        object_name,
        object_type,
        check_name,
        status,
        severity,
        is_virtual,
        object_owner,
        row_count,
        delete_pct,
        error_code,
        error_message,
        probe_sql
    )
        results[#results + 1] = {
            str(check_ts),
            str(database_name),
            str(monitor_user),
            str(schema_name),
            str(object_name),
            str(object_type),
            str(check_name),
            str(status),
            str(severity),
            str(is_virtual),
            str(object_owner),
            str(row_count),
            str(delete_pct),
            str(error_code),
            str(error_message),
            str(probe_sql)
        }
    end

    local function get_error_message(res)
        if res == nil then
            return ''
        end

        if type(res) == 'table' then
            if res.error_message ~= nil then
                return tostring(res.error_message)
            end
            if res['error_message'] ~= nil then
                return tostring(res['error_message'])
            end
        end

        return tostring(res)
    end

    local function get_error_code(res)
        if res == nil then
            return ''
        end

        if type(res) == 'table' then
            if res.error_code ~= nil then
                return tostring(res.error_code)
            end
            if res['error_code'] ~= nil then
                return tostring(res['error_code'])
            end
        end

        return ''
    end

    local function classify_error(error_message)
        local t = string.lower(str(error_message))

        if t == '' then
            return 'INFO'
        elseif string.find(t, 'privilege') or
               string.find(t, 'not authorized') or
               string.find(t, 'permission') then
            return 'CRITICAL'
        elseif string.find(t, 'object') and string.find(t, 'not found') then
            return 'CRITICAL'
        elseif string.find(t, 'connection') or
               string.find(t, 'adapter') or
               string.find(t, 'virtual') then
            return 'CRITICAL'
        elseif string.find(t, 'timeout') or
               string.find(t, 'cancel') or
               string.find(t, 'interrupted') then
            return 'WARNING'
        else
            return 'ERROR'
        end
    end

    local check_ts = query([[SELECT CAST(CURRENT_TIMESTAMP AS VARCHAR(40)) FROM DUAL]])[1][1]
    local database_name = str(exa.meta.database_name)
    local monitor_user = str(exa.meta.current_user)

    ---------------------------------------------------------------------------
    -- Schema filter
    ---------------------------------------------------------------------------

    local schema_filter_for_schemas = ''
    local schema_filter_for_tables  = ''
    local schema_filter_for_views   = ''
    local schema_filter_for_scripts = ''
    local schema_filter_for_funcs   = ''

    if not include_system_schemas then
        schema_filter_for_schemas = [[
            WHERE SCHEMA_NAME NOT LIKE 'EXA_%'
              AND SCHEMA_NAME <> 'SYS'
        ]]

        schema_filter_for_tables = [[
            AND TABLE_SCHEMA NOT LIKE 'EXA_%'
            AND TABLE_SCHEMA <> 'SYS'
        ]]

        schema_filter_for_views = [[
            AND VIEW_SCHEMA NOT LIKE 'EXA_%'
            AND VIEW_SCHEMA <> 'SYS'
        ]]

        schema_filter_for_scripts = [[
            AND SCRIPT_SCHEMA NOT LIKE 'EXA_%'
            AND SCRIPT_SCHEMA <> 'SYS'
        ]]

        schema_filter_for_funcs = [[
            AND FUNCTION_SCHEMA NOT LIKE 'EXA_%'
            AND FUNCTION_SCHEMA <> 'SYS'
        ]]
    end

    ---------------------------------------------------------------------------
    -- 1. Schema-level checks
    --    OPEN SCHEMA tests schema usability.
    --    EXA_ALL_SCHEMA_OBJECTS tests schema object metadata enumeration.
    ---------------------------------------------------------------------------

    local schema_sql = [[
        SELECT
            SCHEMA_NAME,
            SCHEMA_OWNER,
            SCHEMA_IS_VIRTUAL
        FROM EXA_ALL_SCHEMAS
    ]] .. schema_filter_for_schemas .. [[
        ORDER BY SCHEMA_NAME
    ]]

    local ok_schema_list, schema_rows = pquery(schema_sql)

    if not ok_schema_list then
        local msg = get_error_message(schema_rows)
        add_row(
            check_ts,
            database_name,
            monitor_user,
            '',
            '',
            'CATALOG',
            'LIST_SCHEMAS',
            'FAILED',
            classify_error(msg),
            '',
            '',
            '',
            '',
            get_error_code(schema_rows),
            msg,
            schema_sql
        )

        exit(results,
            [[
                CHECK_TS VARCHAR(40),
                DATABASE_NAME VARCHAR(128),
                MONITOR_USER VARCHAR(128),
                SCHEMA_NAME VARCHAR(128),
                OBJECT_NAME VARCHAR(128),
                OBJECT_TYPE VARCHAR(40),
                CHECK_NAME VARCHAR(40),
                STATUS VARCHAR(20),
                SEVERITY VARCHAR(20),
                IS_VIRTUAL VARCHAR(10),
                OBJECT_OWNER VARCHAR(128),
                ROW_COUNT VARCHAR(40),
                DELETE_PCT VARCHAR(40),
                ERROR_CODE VARCHAR(100),
                ERROR_MESSAGE VARCHAR(2000000),
                PROBE_SQL VARCHAR(2000000)
            ]]
        )
    end

    for i = 1, #schema_rows do
        local schema_name = schema_rows[i][1]
        local schema_owner = schema_rows[i][2]
        local schema_is_virtual = schema_rows[i][3]

        local open_schema_sql = 'OPEN SCHEMA ' .. qid(schema_name)
        local ok_open, open_res = pquery([[OPEN SCHEMA ::s]], {s = qid(schema_name)})

        if ok_open then
            add_row(
                check_ts,
                database_name,
                monitor_user,
                schema_name,
                '',
                'SCHEMA',
                'OPEN_SCHEMA',
                'OK',
                'OK',
                schema_is_virtual,
                schema_owner,
                '',
                '',
                '',
                '',
                open_schema_sql
            )
        else
            local msg = get_error_message(open_res)
            add_row(
                check_ts,
                database_name,
                monitor_user,
                schema_name,
                '',
                'SCHEMA',
                'OPEN_SCHEMA',
                'FAILED',
                classify_error(msg),
                schema_is_virtual,
                schema_owner,
                '',
                '',
                get_error_code(open_res),
                msg,
                open_schema_sql
            )
        end

        -- This mimics what many SQL tools do when expanding a schema tree.
        local schema_objects_sql = 'SELECT COUNT(*) FROM EXA_ALL_SCHEMA_OBJECTS AFTER OPEN SCHEMA ' .. qid(schema_name)
        local ok_schema_objects, schema_objects_res =
            pquery([[SELECT COUNT(*) FROM EXA_ALL_SCHEMA_OBJECTS]])

        if ok_schema_objects then
            add_row(
                check_ts,
                database_name,
                monitor_user,
                schema_name,
                '',
                'SCHEMA',
                'LIST_SCHEMA_OBJECTS',
                'OK',
                'OK',
                schema_is_virtual,
                schema_owner,
                str(schema_objects_res[1][1]),
                '',
                '',
                '',
                schema_objects_sql
            )
        else
            local msg = get_error_message(schema_objects_res)
            add_row(
                check_ts,
                database_name,
                monitor_user,
                schema_name,
                '',
                'SCHEMA',
                'LIST_SCHEMA_OBJECTS',
                'FAILED',
                classify_error(msg),
                schema_is_virtual,
                schema_owner,
                '',
                '',
                get_error_code(schema_objects_res),
                msg,
                schema_objects_sql
            )
        end
    end

    ---------------------------------------------------------------------------
    -- 2. Table/view/virtual table object checks
    --    SELECT * WHERE 1=0 compiles the object and resolves its columns,
    --    but does not scan data.
    ---------------------------------------------------------------------------

    local object_sql = [[
        SELECT
            TABLE_SCHEMA AS SCHEMA_NAME,
            TABLE_NAME AS OBJECT_NAME,
            CASE
                WHEN TABLE_IS_VIRTUAL THEN 'VIRTUAL_TABLE'
                ELSE 'TABLE'
            END AS OBJECT_TYPE,
            TABLE_OWNER AS OBJECT_OWNER,
            CAST(TABLE_OBJECT_ID AS VARCHAR(40)) AS OBJECT_ID,
            CAST(TABLE_IS_VIRTUAL AS VARCHAR(10)) AS IS_VIRTUAL,
            CAST(TABLE_ROW_COUNT AS VARCHAR(40)) AS ROW_COUNT,
            CAST(DELETE_PERCENTAGE AS VARCHAR(40)) AS DELETE_PCT
        FROM EXA_ALL_TABLES
        WHERE 1 = 1
    ]] .. schema_filter_for_tables .. [[

        UNION ALL

        SELECT
            VIEW_SCHEMA AS SCHEMA_NAME,
            VIEW_NAME AS OBJECT_NAME,
            'VIEW' AS OBJECT_TYPE,
            VIEW_OWNER AS OBJECT_OWNER,
            CAST(VIEW_OBJECT_ID AS VARCHAR(40)) AS OBJECT_ID,
            'FALSE' AS IS_VIRTUAL,
            '' AS ROW_COUNT,
            '' AS DELETE_PCT
        FROM EXA_ALL_VIEWS
        WHERE 1 = 1
    ]] .. schema_filter_for_views .. [[

        ORDER BY SCHEMA_NAME, OBJECT_TYPE, OBJECT_NAME
    ]]

    local ok_object_list, object_rows = pquery(object_sql)

    if not ok_object_list then
        local msg = get_error_message(object_rows)
        add_row(
            check_ts,
            database_name,
            monitor_user,
            '',
            '',
            'CATALOG',
            'LIST_TABLES_AND_VIEWS',
            'FAILED',
            classify_error(msg),
            '',
            '',
            '',
            '',
            get_error_code(object_rows),
            msg,
            object_sql
        )
    else
        for i = 1, #object_rows do
            local schema_name  = object_rows[i][1]
            local object_name  = object_rows[i][2]
            local object_type  = object_rows[i][3]
            local object_owner = object_rows[i][4]
            local object_id    = object_rows[i][5]
            local is_virtual   = object_rows[i][6]
            local row_count    = object_rows[i][7]
            local delete_pct   = object_rows[i][8]

            local full_name = fq(schema_name, object_name)

            -------------------------------------------------------------------
            -- Runtime/compile probe.
            --
            -- SELECT * is deliberate:
            --   SELECT 1 only checks the FROM object minimally.
            --   SELECT * forces column metadata and view output resolution.
            --
            -- WHERE 1=0 is deliberate:
            --   The object is compiled and authorized, but data is not scanned.
            -------------------------------------------------------------------

            local access_probe_sql = 'SELECT * FROM ' .. full_name .. ' WHERE 1 = 0'
            local ok_access, access_res =
                pquery([[SELECT * FROM ::obj WHERE 1 = 0]], {obj = full_name})

            if ok_access then
                add_row(
                    check_ts,
                    database_name,
                    monitor_user,
                    schema_name,
                    object_name,
                    object_type,
                    'OBJECT_ACCESS_PROBE',
                    'OK',
                    'OK',
                    is_virtual,
                    object_owner,
                    row_count,
                    delete_pct,
                    '',
                    '',
                    access_probe_sql
                )
            else
                local msg = get_error_message(access_res)
                add_row(
                    check_ts,
                    database_name,
                    monitor_user,
                    schema_name,
                    object_name,
                    object_type,
                    'OBJECT_ACCESS_PROBE',
                    'FAILED',
                    classify_error(msg),
                    is_virtual,
                    object_owner,
                    row_count,
                    delete_pct,
                    get_error_code(access_res),
                    msg,
                    access_probe_sql
                )
            end

            -------------------------------------------------------------------
            -- Column metadata probe.
            -- This catches IDE/browser-like failures when expanding an object.
            -------------------------------------------------------------------

            local column_probe_sql =
                'SELECT COUNT(*) FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = ' ..
                qid(schema_name) ..
                ' AND COLUMN_TABLE = ' ..
                qid(object_name)

            local ok_columns, column_res = pquery([[
                SELECT COUNT(*)
                FROM EXA_ALL_COLUMNS
                WHERE COLUMN_SCHEMA = :s
                  AND COLUMN_TABLE = :o
            ]], {s = schema_name, o = object_name})

            if ok_columns then
                add_row(
                    check_ts,
                    database_name,
                    monitor_user,
                    schema_name,
                    object_name,
                    object_type,
                    'COLUMN_METADATA',
                    'OK',
                    'OK',
                    is_virtual,
                    object_owner,
                    str(column_res[1][1]),
                    delete_pct,
                    '',
                    '',
                    column_probe_sql
                )
            else
                local msg = get_error_message(column_res)
                add_row(
                    check_ts,
                    database_name,
                    monitor_user,
                    schema_name,
                    object_name,
                    object_type,
                    'COLUMN_METADATA',
                    'FAILED',
                    classify_error(msg),
                    is_virtual,
                    object_owner,
                    row_count,
                    delete_pct,
                    get_error_code(column_res),
                    msg,
                    column_probe_sql
                )
            end
        end
    end

    ---------------------------------------------------------------------------
    -- 3. Metadata-only object checks.
    --
    -- Scripts/functions/connections are database objects, but executing them
    -- blindly is not safe in production:
    --   - scripts can modify data or users
    --   - functions require arguments
    --   - connections may expose credentials or trigger remote calls indirectly
    ---------------------------------------------------------------------------

    if include_metadata_only_objects then

        local scripts_sql = [[
            SELECT
                SCRIPT_SCHEMA,
                SCRIPT_NAME,
                SCRIPT_TYPE,
                SCRIPT_LANGUAGE,
                SCRIPT_RESULT_TYPE,
                SCRIPT_OWNER
            FROM EXA_ALL_SCRIPTS
            WHERE 1 = 1
        ]] .. schema_filter_for_scripts .. [[
            ORDER BY SCRIPT_SCHEMA, SCRIPT_NAME
        ]]

        local ok_scripts, script_rows = pquery(scripts_sql)

        if ok_scripts then
            for i = 1, #script_rows do
                add_row(
                    check_ts,
                    database_name,
                    monitor_user,
                    script_rows[i][1],
                    script_rows[i][2],
                    'SCRIPT:' .. str(script_rows[i][3]),
                    'SCRIPT_METADATA',
                    'METADATA_ONLY',
                    'INFO',
                    '',
                    script_rows[i][6],
                    '',
                    '',
                    '',
                    'Metadata visible. Script was not executed because production monitoring must not run arbitrary scripts.',
                    'SELECT * FROM EXA_ALL_SCRIPTS WHERE SCRIPT_SCHEMA = ' ..
                        qid(script_rows[i][1]) ..
                        ' AND SCRIPT_NAME = ' ..
                        qid(script_rows[i][2])
                )
            end
        else
            local msg = get_error_message(script_rows)
            add_row(
                check_ts,
                database_name,
                monitor_user,
                '',
                '',
                'CATALOG',
                'LIST_SCRIPTS',
                'FAILED',
                classify_error(msg),
                '',
                '',
                '',
                '',
                get_error_code(script_rows),
                msg,
                scripts_sql
            )
        end

        local functions_sql = [[
            SELECT
                FUNCTION_SCHEMA,
                FUNCTION_NAME,
                FUNCTION_OWNER
            FROM EXA_ALL_FUNCTIONS
            WHERE 1 = 1
        ]] .. schema_filter_for_funcs .. [[
            ORDER BY FUNCTION_SCHEMA, FUNCTION_NAME
        ]]

        local ok_functions, function_rows = pquery(functions_sql)

        if ok_functions then
            for i = 1, #function_rows do
                add_row(
                    check_ts,
                    database_name,
                    monitor_user,
                    function_rows[i][1],
                    function_rows[i][2],
                    'FUNCTION',
                    'FUNCTION_METADATA',
                    'METADATA_ONLY',
                    'INFO',
                    '',
                    function_rows[i][3],
                    '',
                    '',
                    '',
                    'Metadata visible. Function was not executed because argument values and side effects are not known.',
                    'SELECT * FROM EXA_ALL_FUNCTIONS WHERE FUNCTION_SCHEMA = ' ..
                        qid(function_rows[i][1]) ..
                        ' AND FUNCTION_NAME = ' ..
                        qid(function_rows[i][2])
                )
            end
        else
            local msg = get_error_message(function_rows)
            add_row(
                check_ts,
                database_name,
                monitor_user,
                '',
                '',
                'CATALOG',
                'LIST_FUNCTIONS',
                'FAILED',
                classify_error(msg),
                '',
                '',
                '',
                '',
                get_error_code(function_rows),
                msg,
                functions_sql
            )
        end

        local connections_sql = [[
            SELECT
                CONNECTION_NAME
            FROM EXA_ALL_CONNECTIONS
            ORDER BY CONNECTION_NAME
        ]]

        local ok_connections, connection_rows = pquery(connections_sql)

        if ok_connections then
            for i = 1, #connection_rows do
                add_row(
                    check_ts,
                    database_name,
                    monitor_user,
                    '',
                    connection_rows[i][1],
                    'CONNECTION',
                    'CONNECTION_METADATA',
                    'METADATA_ONLY',
                    'INFO',
                    '',
                    '',
                    '',
                    '',
                    '',
                    'Metadata visible. Connection was not used because using it may trigger remote access or expose external dependency failures.',
                    'SELECT * FROM EXA_ALL_CONNECTIONS WHERE CONNECTION_NAME = ' ..
                        qid(connection_rows[i][1])
                )
            end
        else
            local msg = get_error_message(connection_rows)
            add_row(
                check_ts,
                database_name,
                monitor_user,
                '',
                '',
                'CATALOG',
                'LIST_CONNECTIONS',
                'FAILED',
                classify_error(msg),
                '',
                '',
                '',
                '',
                get_error_code(connection_rows),
                msg,
                connections_sql
            )
        end
    end

    ---------------------------------------------------------------------------
    -- Return final result table.
    ---------------------------------------------------------------------------

    exit(results,
        [[
            CHECK_TS VARCHAR(40),
            DATABASE_NAME VARCHAR(128),
            MONITOR_USER VARCHAR(128),
            SCHEMA_NAME VARCHAR(128),
            OBJECT_NAME VARCHAR(128),
            OBJECT_TYPE VARCHAR(40),
            CHECK_NAME VARCHAR(40),
            STATUS VARCHAR(20),
            SEVERITY VARCHAR(20),
            IS_VIRTUAL VARCHAR(10),
            OBJECT_OWNER VARCHAR(128),
            ROW_COUNT VARCHAR(40),
            DELETE_PCT VARCHAR(40),
            ERROR_CODE VARCHAR(100),
            ERROR_MESSAGE VARCHAR(2000000),
            PROBE_SQL VARCHAR(2000000)
        ]]
    )
/