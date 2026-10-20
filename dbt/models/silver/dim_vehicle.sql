{# One row per standardized make, model and model year that appears in a vehicle complaint or recall. #}

with vehicle_rows as (

    select vehicle_name_key, model_year
    from {{ ref('complaint_components') }}
    where product_type = 'V'
    union
    select vehicle_name_key, model_year
    from {{ ref('recall_components') }}
    where product_type = 'V'

)

select distinct
    md5(coalesce(m.make, '') || '|' || coalesce(m.model, '') || '|' || coalesce(v.model_year::varchar, '')) as vehicle_id,
    m.make,
    m.model,
    m.model_family,
    v.model_year
from vehicle_rows as v
inner join {{ ref('vehicle_name_map') }} as m
    on m.vehicle_name_key = v.vehicle_name_key
