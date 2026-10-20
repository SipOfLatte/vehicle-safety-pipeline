{#
  One row per recall campaign, product, vehicle and component. Bronze repeats a
  combination once per manufacturer part number; those duplicates are dropped here.
#}

with deduped as (

    select distinct
        trim(campno) as campaign_number,
        nullif(upper(trim(rcltypecd)), '') as product_type,
        {{ clean_name('maketxt') }} as reported_make,
        {{ clean_name('modeltxt') }} as reported_model,
        {{ model_year('yeartxt') }} as model_year,
        {{ clean_name('compname') }} as component_name
    from {{ source('bronze', 'recalls') }}

)

select
    *,
    {{ vehicle_name_key('reported_make', 'reported_model') }} as vehicle_name_key,
    md5(coalesce(component_name, '')) as component_key
from deduped
