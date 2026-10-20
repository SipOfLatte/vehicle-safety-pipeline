{#
  One row per complaint, product and component, with exact duplicate rows removed.
  Names are cleaned (case, whitespace) but not standardized here: vehicle_name_map and
  dim_component do that, joined on the keys below, so a mapping change applies without
  rebuilding this table.

  Incremental: rebuilds the rows of complaints that complaints.sql just inserted or changed.
#}
-- depends_on: {{ ref('complaints') }}
{{ config(
    materialized='incremental',
    unique_key='odino',
    incremental_strategy='delete+insert',
) }}

with deduped as (

    select distinct
        trim(odino) as odino,
        nullif(upper(trim(prod_type)), '') as product_type,
        {{ clean_name('maketxt') }} as reported_make,
        {{ clean_name('modeltxt') }} as reported_model,
        {{ model_year('yeartxt') }} as model_year,
        {{ clean_name('compdesc') }} as component_name
    from {{ source('bronze', 'complaints') }}
    {% if is_incremental() %}
    where trim(odino) in (
        select odino
        from {{ ref('complaints') }}
        where updated_at > (select coalesce(max(updated_at), '1900-01-01'::timestamp_ltz) from {{ this }})
    )
    {% endif %}

)

select
    odino,
    product_type,
    reported_make,
    reported_model,
    model_year,
    component_name,
    {{ vehicle_name_key('reported_make', 'reported_model') }} as vehicle_name_key,
    md5(coalesce(component_name, '')) as component_key,
    current_timestamp() as updated_at
from deduped
