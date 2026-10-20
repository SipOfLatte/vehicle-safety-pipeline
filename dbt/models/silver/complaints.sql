{#
  One row per complaint (ODINO). Bronze has one row per complaint, product and component,
  so complaint-level fields are rolled up here.

  Incremental: bronze is a full snapshot every day, so each run hashes every complaint's
  source rows and only merges complaints that are new or whose rows changed. CMPLID is
  left out of the hash because NHTSA can renumber it without anything else changing.
  Complaints NHTSA has removed are deleted by the post-hook.
#}
{{ config(
    materialized='incremental',
    unique_key='odino',
    incremental_strategy='merge',
    on_schema_change='append_new_columns',
    post_hook="{{ delete_complaints_missing_from_bronze() if is_incremental() }}",
) }}

{% set hashed_cols = [
    'odino', 'mfr_name', 'maketxt', 'modeltxt', 'yeartxt', 'crash', 'faildate', 'fire',
    'injured', 'deaths', 'compdesc', 'city', 'state', 'vin', 'datea', 'ldate', 'miles',
    'occurences', 'cdescr', 'cmpl_type', 'police_rpt_yn', 'purch_dt', 'orig_owner_yn',
    'anti_brakes_yn', 'cruise_cont_yn', 'num_cyls', 'drive_train', 'fuel_sys', 'fuel_type',
    'trans_type', 'veh_speed', 'prod_type', 'medical_attn', 'vehicles_towed_yn',
    'state_of_incident',
] %}

with source_rows as (

    select
        trim(odino) as odino,
        {{ nhtsa_date('ldate') }} as received_date,
        {{ nhtsa_date('faildate') }} as incident_date,
        {{ nhtsa_date('datea') }} as added_date,
        {{ yn('crash') }} as crash,
        {{ yn('fire') }} as fire,
        {{ yn('police_rpt_yn') }} as police_report,
        {{ yn('medical_attn') }} as medical_attention,
        {{ yn('vehicles_towed_yn') }} as vehicle_towed,
        try_to_number(injured) as injured,
        try_to_number(deaths) as deaths,
        try_to_number(miles) as miles,
        try_to_number(veh_speed) as vehicle_speed,
        nullif(upper(trim(cmpl_type)), '') as complaint_source_code,
        nullif(upper(trim(state)), '') as consumer_state,
        nullif(upper(trim(state_of_incident)), '') as incident_state,
        nullif(trim(cdescr), '') as summary,
        {{ row_md5(hashed_cols) }} as row_md5
    from {{ source('bronze', 'complaints') }}

),

complaints as (

    select
        odino,
        min(received_date) as received_date,
        min(incident_date) as incident_date,
        min(added_date) as added_date,
        boolor_agg(crash) as crash,
        boolor_agg(fire) as fire,
        boolor_agg(police_report) as police_report,
        boolor_agg(medical_attention) as medical_attention,
        boolor_agg(vehicle_towed) as vehicle_towed,
        max(injured) as injured,
        max(deaths) as deaths,
        max(miles) as miles,
        max(vehicle_speed) as vehicle_speed,
        max(complaint_source_code) as complaint_source_code,
        max(consumer_state) as consumer_state,
        max(incident_state) as incident_state,
        -- A complaint's rows can carry different text (about 17,000 complaints); keep the longest.
        max_by(summary, length(summary)) as summary,
        count(distinct summary) > 1 as has_multiple_summaries,
        count(*) as source_row_count,
        md5(listagg(row_md5, '') within group (order by row_md5)) as row_hash
    from source_rows
    group by odino

)

select
    c.*,
    current_timestamp() as updated_at
from complaints as c
{% if is_incremental() %}
left join {{ this }} as existing
    on existing.odino = c.odino
where existing.odino is null
   or existing.row_hash <> c.row_hash
{% endif %}
