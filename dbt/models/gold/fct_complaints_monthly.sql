{#
  Vehicle complaints per month received, make, model family, model year and component group.
  A complaint about two component groups counts once in each, so summing across component
  groups counts some complaints more than once.
#}

select
    date_trunc('month', c.received_date)::date as month,
    cvc.make,
    cvc.model_family,
    cvc.model_year,
    cvc.component_group,
    count(*) as complaints,
    count_if(c.crash) as crash_complaints,
    count_if(c.fire) as fire_complaints,
    count_if(c.injured > 0) as injury_complaints,
    count_if(c.deaths > 0) as fatal_complaints,
    coalesce(sum(c.injured), 0) as injured,
    coalesce(sum(c.deaths), 0) as deaths
from {{ ref('complaint_vehicle_components') }} as cvc
inner join {{ ref('complaints') }} as c
    on c.odino = cvc.odino
group by all
