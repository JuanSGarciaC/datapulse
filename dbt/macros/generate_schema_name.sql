{# Usa el nombre de esquema tal cual (staging, marts) en lugar de "<target>_<custom>".
   Los esquemas los crea la migración 0001 con sus permisos; dbt no debe inventar otros. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ (custom_schema_name or target.schema) | trim }}
{%- endmacro %}
