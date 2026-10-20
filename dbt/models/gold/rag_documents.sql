{#
  Text for the RAG chatbot to embed: one document per vehicle complaint received from 2020
  onward and per recall campaign received from 2010 onward. Metadata columns are arrays
  because one complaint or recall can cover several vehicles. content_hash changes when
  the text or metadata changes, so the embedding job only re-embeds what changed.
#}

-- Not for the text-to-SQL assistant: the gold-wide ASK_ROLE grant is replaced with none.
{{ config(grants={'select': []}) }}

with complaint_meta as (

    select
        odino,
        array_agg(distinct make) within group (order by make) as makes,
        array_agg(distinct model_family) within group (order by model_family) as model_families,
        array_agg(distinct model_year) within group (order by model_year) as model_years,
        array_agg(distinct component_group) within group (order by component_group) as component_groups
    from {{ ref('complaint_vehicle_components') }}
    group by odino

),

complaint_docs as (

    select
        'complaint:' || c.odino as doc_id,
        'complaint' as doc_type,
        c.odino as source_id,
        'NHTSA' as source,
        c.received_date as doc_date,
        cm.makes,
        cm.model_families,
        cm.model_years,
        cm.component_groups,
        c.crash or c.fire or c.injured > 0 or c.deaths > 0 as is_severe,
        c.summary as text
    from {{ ref('complaints') }} as c
    inner join complaint_meta as cm
        on cm.odino = c.odino
    where c.received_date >= '2020-01-01'
      and c.summary is not null

),

recall_meta as (

    select
        campaign_number,
        array_agg(distinct make) within group (order by make) as makes,
        array_agg(distinct model_family) within group (order by model_family) as model_families,
        array_agg(distinct model_year) within group (order by model_year) as model_years,
        array_agg(distinct component_group) within group (order by component_group) as component_groups
    from {{ ref('recall_vehicle_components') }}
    group by campaign_number

),

recall_docs as (

    select
        'recall:' || r.campaign_number as doc_id,
        'recall' as doc_type,
        r.campaign_number as source_id,
        r.source,
        r.report_received_date as doc_date,
        rm.makes,
        rm.model_families,
        rm.model_years,
        rm.component_groups,
        r.do_not_drive or r.park_outside as is_severe,
        concat_ws(
            '\n\n',
            'Defect: ' || coalesce(r.defect_summary, ''),
            'Consequence: ' || coalesce(r.consequence_summary, ''),
            'Remedy: ' || coalesce(r.corrective_action, '')
        ) as text
    from {{ ref('recalls') }} as r
    inner join recall_meta as rm
        on rm.campaign_number = r.campaign_number
    where r.report_received_date >= '2010-01-01'

),

docs as (
    select * from complaint_docs
    union all
    select * from recall_docs
)

select
    *,
    md5(
        text || '|' || to_json(makes) || '|' || to_json(model_families) || '|'
        || to_json(model_years) || '|' || to_json(component_groups)
    ) as content_hash
from docs
