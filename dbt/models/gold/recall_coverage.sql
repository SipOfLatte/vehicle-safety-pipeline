{#
  Complaints matched to recalls on make, model family, model year and component group.
  Rows with many complaints and no recall are complaint clusters NHTSA hasn't recalled.
  The match is by component group, so it shows overlap, not proof that a recall fixes
  the problem owners describe.
#}

with complaint_groups as (

    select
        cvc.make,
        cvc.model_family,
        cvc.model_year,
        cvc.component_group,
        count(*) as complaints,
        count_if(c.crash or c.fire or c.injured > 0 or c.deaths > 0) as severe_complaints,
        min(c.received_date) as first_complaint_date,
        max(c.received_date) as latest_complaint_date
    from {{ ref('complaint_vehicle_components') }} as cvc
    inner join {{ ref('complaints') }} as c
        on c.odino = cvc.odino
    where cvc.model_year is not null
    group by all

),

recall_groups as (

    select
        make,
        model_family,
        model_year,
        component_group,
        count(distinct campaign_number) as recall_campaigns,
        array_agg(distinct campaign_number) within group (order by campaign_number) as campaign_numbers
    from {{ ref('recall_vehicle_components') }}
    where model_year is not null
    group by all

)

select
    cg.*,
    coalesce(rg.recall_campaigns, 0) as recall_campaigns,
    coalesce(rg.recall_campaigns, 0) > 0 as has_recall,
    rg.campaign_numbers
from complaint_groups as cg
left join recall_groups as rg
    on rg.make = cg.make
   and rg.model_family = cg.model_family
   and rg.model_year = cg.model_year
   and rg.component_group = cg.component_group
