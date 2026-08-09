import { useQuery } from '@tanstack/react-query'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'
import { Text } from '@astryxdesign/core/Text'
import { Spinner } from '@astryxdesign/core/Spinner'

import { Card, PageHeader } from '@/components/ui'
import { api } from '@/lib/api'

export default function Settings() {
  const { data, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<Record<string, string | null>>('/settings'),
  })

  return (
    <VStack gap={4}>
      <PageHeader
        title="Settings"
        subtitle="Read-only deployment configuration (set via environment variables)"
      />
      <Card>
        {isLoading ? (
          <VStack gap={4} hAlign="center" vAlign="center" style={{ padding: '2rem 0' }}>
            <Spinner size="md" />
          </VStack>
        ) : (
          <Grid columns={2} gap={3}>
            {Object.entries(data ?? {}).map(([key, value]) => (
              <HStack key={key} vAlign="center" hAlign="between" style={{ borderBottom: '1px solid var(--color-border, #e2e8f0)', paddingBottom: '0.5rem' }}>
                <Text type="supporting" color="secondary">{key}</Text>
                <Text type="code">{value ?? '—'}</Text>
              </HStack>
            ))}
          </Grid>
        )}
      </Card>
    </VStack>
  )
}
