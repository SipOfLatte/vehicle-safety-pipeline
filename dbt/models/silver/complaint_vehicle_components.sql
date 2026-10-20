{# Vehicle complaints with standardized names: one row per complaint, vehicle and component group. #}
{{ config(materialized='view') }}

select distinct
    cc.odino,
    m.make,
    m.model,
    m.model_family,
    cc.model_year,
    d.component_group
from {{ ref('complaint_components') }} as cc
inner join {{ ref('vehicle_name_map') }} as m
    on m.vehicle_name_key = cc.vehicle_name_key
inner join {{ ref('dim_component') }} as d
    on d.component_key = cc.component_key
where cc.product_type = 'V'
  and m.make is not null  -- 2 complaints have no make, model or year at all
