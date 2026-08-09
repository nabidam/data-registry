import { useQuery } from '@tanstack/react-query'
import { VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'
import { Heading } from '@astryxdesign/core/Text'

import { DataTable } from '@/components/DataTable'
import { Card, PageHeader } from '@/components/ui'
import { api } from '@/lib/api'
import type { Row } from '@/types'

function StatTable({ title, path, columns }: { title: string; path: string; columns: string[] }) {
  const { data, isLoading } = useQuery({
    queryKey: ['stats', path],
    queryFn: () => api.get<Row[]>(path),
  })
  return (
    <Card>
      <VStack gap={3}>
        <Heading level={4}>{title}</Heading>
        <DataTable
          loading={isLoading}
          rows={data}
          columns={columns.map((c) => ({ key: c, header: c.replace(/_/g, ' ') }))}
        />
      </VStack>
    </Card>
  )
}

export default function Statistics() {
  return (
    <VStack gap={4}>
      <PageHeader title="Statistics" subtitle="Registry-wide counts, served from Postgres" />
      <Grid columns={2} gap={4}>
        <StatTable
          title="By language pair"
          path="/stats/language-pairs"
          columns={['src_lang', 'tgt_lang', 'count']}
        />
        <StatTable title="By domain" path="/stats/domains" columns={['domain', 'count']} />
        <StatTable title="By batch" path="/stats/batches" columns={['id', 'name', 'sample_count']} />
        <StatTable title="By source" path="/stats/sources" columns={['source_id', 'count']} />
      </Grid>
    </VStack>
  )
}
