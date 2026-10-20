{#
  Maps every make/model name NHTSA reports to a standardized make, model and model family.

  1. Make: spellings that differ only in spaces and punctuation (MERCEDES BENZ / MERCEDES-BENZ)
     become the most common spelling.
  2. Model: the same rule within each standardized make (F150 / F-150, C 300 / C300).
  3. Family: trailing powertrain and drivetrain words are dropped (CAMRY HYBRID -> CAMRY,
     PATRIOT 4X4 -> PATRIOT), unless model_family_overrides says otherwise.
#}

with reported as (

    select reported_make, reported_model, count(*) as row_count
    from {{ ref('complaint_components') }}
    group by 1, 2
    union all
    select reported_make, reported_model, count(*)
    from {{ ref('recall_components') }}
    group by 1, 2

),

names as (

    select reported_make, reported_model, sum(row_count) as row_count
    from reported
    group by 1, 2

),

makes as (

    select
        reported_make,
        first_value(reported_make) over (
            partition by {{ alnum_key('reported_make') }}
            order by make_rows desc, reported_make
        ) as make
    from (
        select reported_make, sum(row_count) as make_rows
        from names
        group by 1
    )

),

with_make as (

    select n.reported_make, n.reported_model, n.row_count, m.make
    from names as n
    left join makes as m
        on m.reported_make is not distinct from n.reported_make

),

models as (

    select
        make,
        reported_model,
        first_value(reported_model) over (
            partition by make, {{ alnum_key('reported_model') }}
            order by model_rows desc, reported_model
        ) as model
    from (
        select make, reported_model, sum(row_count) as model_rows
        from with_make
        group by 1, 2
    )

),

standardized as (

    select
        w.reported_make,
        w.reported_model,
        w.row_count,
        w.make,
        m.model,
        regexp_replace(
            m.model,
            '([[:space:]]+(PLUG-IN HYBRID|PLUG IN HYBRID|HYBRID|PHEV|AWD|4WD|2WD|FWD|RWD|4X4|4X2|2-DR|4-DR))+$',
            ''
        ) as rule_family
    from with_make as w
    left join models as m
        on m.make is not distinct from w.make
       and m.reported_model is not distinct from w.reported_model

)

select
    {{ vehicle_name_key('s.reported_make', 's.reported_model') }} as vehicle_name_key,
    s.reported_make,
    s.reported_model,
    s.make,
    s.model,
    coalesce(o.model_family, s.rule_family) as model_family,
    case
        when o.model_family is not null then 'override'
        when s.rule_family <> s.model then 'family_rule'
        when s.model <> s.reported_model or s.make <> s.reported_make then 'spelling_rule'
        else 'as_reported'
    end as mapping_rule,
    s.row_count
from standardized as s
left join {{ ref('model_family_overrides') }} as o
    on o.make = s.make
   and o.model = s.model
