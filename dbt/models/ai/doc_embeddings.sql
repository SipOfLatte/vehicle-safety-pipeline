{#
  Vector embeddings of rag_documents for the chatbot's retrieval, with the metadata copied
  alongside so a search reads one table.

  Incremental: only documents that are new or whose content_hash changed are embedded, so the
  daily run pays for a day's new complaints, not all ~440k documents. Documents that drop out
  of rag_documents are deleted by the post-hook. Disabled until enable_embeddings is true.
#}
{{ config(
    enabled=var('enable_embeddings'),
    materialized='incremental',
    unique_key='doc_id',
    incremental_strategy='merge',
    on_schema_change='fail',
    post_hook="{% if is_incremental() %}delete from {{ this }} as t where not exists (select 1 from {{ ref('rag_documents') }} as d where d.doc_id = t.doc_id){% endif %}",
) }}

select
    d.doc_id,
    d.doc_type,
    d.source_id,
    d.source,
    d.doc_date,
    d.makes,
    d.model_families,
    d.model_years,
    d.component_groups,
    d.is_severe,
    d.text,
    d.content_hash,
    snowflake.cortex.embed_text_1024('{{ var("embedding_model") }}', d.text) as embedding,
    '{{ var("embedding_model") }}' as embedding_model,
    current_timestamp() as embedded_at
from {{ ref('rag_documents') }} as d
{% if is_incremental() %}
left join {{ this }} as existing
    on existing.doc_id = d.doc_id
where existing.doc_id is null
   or existing.content_hash <> d.content_hash
{% endif %}
