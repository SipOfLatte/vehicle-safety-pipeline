{#
  Monthly complaints per make, model family and component group (all model years together),
  compared with the average of the three months before. Covers the last 36 complete months;
  the current month is left out because it is still filling up.
  A month with no complaints counts as 0 in the average.
#}

{% set rising_min_complaints = 10 %}
{% set rising_ratio = 2 %}

with monthly as (

    select make, model_family, component_group, month, sum(complaints) as complaints
    from {{ ref('fct_complaints_monthly') }}
    where month >= dateadd('month', -39, date_trunc('month', current_date()))
      and month < date_trunc('month', current_date())
    group by all

),

with_prior as (

    select
        m.make,
        m.model_family,
        m.component_group,
        m.month,
        m.complaints,
        coalesce(sum(p.complaints), 0) / 3 as prior_3_month_avg
    from monthly as m
    left join monthly as p
        on p.make = m.make
       and p.model_family = m.model_family
       and p.component_group = m.component_group
       and p.month between dateadd('month', -3, m.month) and dateadd('month', -1, m.month)
    group by all

)

select
    *,
    round(complaints / nullif(prior_3_month_avg, 0), 2) as ratio_to_prior,
    complaints >= {{ rising_min_complaints }}
        and complaints >= {{ rising_ratio }} * prior_3_month_avg as is_rising
from with_prior
where month >= dateadd('month', -36, date_trunc('month', current_date()))
