{#
  One row per component name used in complaints or recalls. NHTSA's names are a
  colon-separated hierarchy, e.g. SERVICE BRAKES, HYDRAULIC:PEDALS AND LINKAGES.
#}

with names as (

    select component_key, component_name from {{ ref('complaint_components') }}
    union
    select component_key, component_name from {{ ref('recall_components') }}

),

split as (

    select
        component_key,
        component_name,
        nullif(trim(split_part(component_name, ':', 1)), '') as component_level1,
        nullif(trim(split_part(component_name, ':', 2)), '') as component_level2,
        nullif(trim(split_part(component_name, ':', 3)), '') as component_level3
    from names

)

select
    s.*,
    coalesce(
        g.component_group,
        case when s.component_level1 is null then 'UNKNOWN OR OTHER' end
    ) as component_group
from split as s
left join {{ ref('component_groups') }} as g
    on g.component_level1 = s.component_level1
