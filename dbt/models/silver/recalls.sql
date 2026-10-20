{#
  One row per recall campaign. Bronze has one row per campaign, vehicle and component;
  the summary text is identical across a campaign's rows (checked 19 Oct 2026).
  Small enough (330k bronze rows) to rebuild in full every day.
#}

select
    trim(campno) as campaign_number,
    'NHTSA' as source,
    max(nullif(trim(mfgcampno), '')) as manufacturer_campaign_number,
    max(nullif(trim(mfgname), '')) as manufacturer,
    max(nullif(upper(trim(rcltypecd)), '')) as recall_type_code,
    max(try_to_number(potaff)) as potentially_affected,
    max(nullif(upper(trim(influenced_by)), '')) as initiated_by,
    max(nullif(trim(fmvss), '')) as fmvss,
    min({{ nhtsa_date('rcdate') }}) as report_received_date,
    min({{ nhtsa_date('odate') }}) as owner_notification_date,
    min({{ nhtsa_date('datea') }}) as record_created_date,
    min({{ nhtsa_date('bgman') }}) as manufacture_begin_date,
    max({{ nhtsa_date('endman') }}) as manufacture_end_date,
    max(nullif(trim(desc_defect), '')) as defect_summary,
    max(nullif(trim(conequence_defect), '')) as consequence_summary,
    max(nullif(trim(corrective_action), '')) as corrective_action,
    max(nullif(trim(notes), '')) as notes,
    boolor_agg(upper(trim(do_not_drive)) = 'YES') as do_not_drive,
    boolor_agg(upper(trim(park_outside)) = 'YES') as park_outside,
    count(*) as source_row_count
from {{ source('bronze', 'recalls') }}
group by trim(campno)
