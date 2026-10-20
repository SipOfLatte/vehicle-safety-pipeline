{# Helpers for NHTSA's raw text columns. Bronze is all VARCHAR. #}

{# Upper case, trimmed, runs of whitespace collapsed; empty becomes NULL. #}
{% macro clean_name(col) -%}
    nullif(regexp_replace(upper(trim({{ col }})), '[[:space:]]+', ' '), '')
{%- endmacro %}

{# Letters and digits only, for grouping spelling variants like F-150 / F150. #}
{% macro alnum_key(col) -%}
    regexp_replace(coalesce({{ col }}, ''), '[^A-Z0-9]', '')
{%- endmacro %}

{# YYYYMMDD text to DATE; anything unparseable becomes NULL. #}
{% macro nhtsa_date(col) -%}
    try_to_date(nullif(trim({{ col }}), ''), 'YYYYMMDD')
{%- endmacro %}

{# 'Y' / 'N' to boolean. Since May 2021 NHTSA writes N for blanks, so false can also mean "not reported". #}
{% macro yn(col) -%}
    case upper(trim({{ col }})) when 'Y' then true when 'N' then false end
{%- endmacro %}

{# 9999 and anything that isn't a 4-digit year become NULL. #}
{% macro model_year(col) -%}
    case when trim({{ col }}) regexp '[0-9]{4}' and trim({{ col }}) <> '9999' then trim({{ col }})::int end
{%- endmacro %}

{# Stable hash of a row; NULLs and empty strings hash the same. #}
{% macro row_md5(cols) -%}
    md5({% for c in cols %}coalesce(trim({{ c }}), ''){% if not loop.last %} || '|' || {% endif %}{% endfor %})
{%- endmacro %}

{% macro vehicle_name_key(make_col, model_col) -%}
    md5(coalesce({{ make_col }}, '') || '|' || coalesce({{ model_col }}, ''))
{%- endmacro %}
