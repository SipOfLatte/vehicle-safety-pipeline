{#
  One row per make, model family and model year: complaint volume, how many complaints were
  severe, and recall campaigns. NHTSA publishes no sales figures, so complaint totals are
  counts, not rates per vehicle sold, and best-selling models rank high on volume alone.
  severe_share (the share of a vehicle's own complaints that report a crash, fire, injury or
  death) doesn't depend on how many were sold, so it leads the table.
#}

with vehicle_complaints as (

    select distinct odino, make, model_family, model_year
    from {{ ref('complaint_vehicle_components') }}

),

complaint_stats as (

    select
        vc.make,
        vc.model_family,
        vc.model_year,
        count(*) as complaints,
        count_if(c.crash or c.fire or c.injured > 0 or c.deaths > 0) as severe_complaints,
        count_if(c.crash) as crash_complaints,
        count_if(c.fire) as fire_complaints,
        coalesce(sum(c.injured), 0) as injured,
        coalesce(sum(c.deaths), 0) as deaths,
        min(c.received_date) as first_complaint_date,
        max(c.received_date) as latest_complaint_date
    from vehicle_complaints as vc
    inner join {{ ref('complaints') }} as c
        on c.odino = vc.odino
    group by all

),

top_component as (

    select make, model_family, model_year, component_group as top_component_group
    from {{ ref('complaint_vehicle_components') }}
    group by all
    qualify row_number() over (
        partition by make, model_family, model_year
        order by count(*) desc, component_group
    ) = 1

),

recall_stats as (

    select
        rvc.make,
        rvc.model_family,
        rvc.model_year,
        count(distinct rvc.campaign_number) as recall_campaigns,
        count(distinct case when r.do_not_drive then rvc.campaign_number end) as do_not_drive_recalls,
        max(r.report_received_date) as latest_recall_date
    from {{ ref('recall_vehicle_components') }} as rvc
    inner join {{ ref('recalls') }} as r
        on r.campaign_number = rvc.campaign_number
    group by all

)

select
    cs.make,
    cs.model_family,
    cs.model_year,
    round(cs.severe_complaints / cs.complaints, 4) as severe_share,
    cs.complaints,
    round(percent_rank() over (partition by cs.model_year order by cs.complaints), 4)
        as complaints_percentile_in_model_year,
    cs.severe_complaints,
    cs.crash_complaints,
    cs.fire_complaints,
    cs.injured,
    cs.deaths,
    tc.top_component_group,
    coalesce(rs.recall_campaigns, 0) as recall_campaigns,
    coalesce(rs.do_not_drive_recalls, 0) as do_not_drive_recalls,
    cs.first_complaint_date,
    cs.latest_complaint_date,
    rs.latest_recall_date
from complaint_stats as cs
left join top_component as tc
    on tc.make = cs.make
   and tc.model_family = cs.model_family
   and tc.model_year is not distinct from cs.model_year
left join recall_stats as rs
    on rs.make = cs.make
   and rs.model_family = cs.model_family
   and rs.model_year is not distinct from cs.model_year
