-- Silver must hold exactly the complaints in today's bronze snapshot: none missing, none left over.
-- Returns one row per complaint that is in one but not the other.

with bronze as (
    select distinct trim(odino) as odino from {{ source('bronze', 'complaints') }}
),

silver as (
    select odino from {{ ref('complaints') }}
)

select coalesce(b.odino, s.odino) as odino,
       case when s.odino is null then 'missing from silver' else 'not in bronze' end as problem
from bronze as b
full outer join silver as s
    on s.odino = b.odino
where b.odino is null or s.odino is null
