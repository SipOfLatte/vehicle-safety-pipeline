{# Vehicle recalls with standardized names: one row per campaign, vehicle and component group. #}
{{ config(materialized='view') }}

select distinct
    rc.campaign_number,
    m.make,
    m.model,
    m.model_family,
    rc.model_year,
    d.component_group
from {{ ref('recall_components') }} as rc
inner join {{ ref('vehicle_name_map') }} as m
    on m.vehicle_name_key = rc.vehicle_name_key
inner join {{ ref('dim_component') }} as d
    on d.component_key = rc.component_key
where rc.product_type = 'V'
  and m.make is not null
