import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  type Connection,
  type EdgeChange,
  type Node,
  type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Play, Save, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { NodePalette } from '@/features/strategy-builder/NodePalette'
import { nodeTypes } from '@/features/strategy-builder/nodes/StrategyNodes'
import { useStrategyStore, type StrategyNodeData } from '@/store/useStrategyStore'
import { api } from '@/lib/api'

let nodeId = 100

export function StrategyCanvas() {
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const {
    strategyName,
    nodes,
    edges,
    isDirty,
    setStrategyName,
    setNodes,
    setEdges,
    markSaved,
    removeNode,
  } = useStrategyStore()
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([])
  const [testStatus, setTestStatus] = useState('Ready')
  const [signalOutput, setSignalOutput] = useState<string[]>([])
  const [isExecuting, setIsExecuting] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [savedStrategyId, setSavedStrategyId] = useState<string | null>(null)

  const onNodesChange = useCallback(
    (changes: NodeChange<Node<StrategyNodeData>>[]) => {
      setNodes((currentNodes) => applyNodeChanges(changes, currentNodes))
    },
    [setNodes]
  )

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      setEdges((currentEdges) => applyEdgeChanges(changes, currentEdges))
    },
    [setEdges]
  )

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((currentEdges) => addEdge({ ...connection, animated: true }, currentEdges))
    },
    [setEdges]
  )

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()
      const type = event.dataTransfer.getData('application/reactflow-type')
      const label = event.dataTransfer.getData('application/reactflow-label')
      const paramsStr = event.dataTransfer.getData('application/reactflow-params')

      if (!type || !reactFlowWrapper.current) return

      const params = JSON.parse(paramsStr) as Record<string, string | number>
      const bounds = reactFlowWrapper.current.getBoundingClientRect()
      const position = {
        x: event.clientX - bounds.left - 90,
        y: event.clientY - bounds.top - 40,
      }

      const newNode: Node<StrategyNodeData> = {
        id: `${type}-${nodeId++}`,
        type,
        position,
        data: { label, nodeType: type as StrategyNodeData['nodeType'], params },
      }

      setNodes([...nodes, newNode])
    },
    [nodes, setNodes]
  )

  const onDragStart = (
    event: React.DragEvent,
    nodeType: string,
    label: string,
    params: Record<string, string | number>
  ) => {
    event.dataTransfer.setData('application/reactflow-type', nodeType)
    event.dataTransfer.setData('application/reactflow-label', label)
    event.dataTransfer.setData('application/reactflow-params', JSON.stringify(params))
    event.dataTransfer.effectAllowed = 'move'
  }

  const buildGraphPayload = () => {
    return {
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.type,
        position: n.position,
        data: n.data,
      })),
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        animated: e.animated,
      })),
    }
  }

  const handleSave = async () => {
    setIsSaving(true)
    try {
      const graph = buildGraphPayload()
      const result = await api.strategies.create({
        name: strategyName,
        description: '',
        graph,
      })
      setSavedStrategyId(result.id)
      markSaved()
      setTestStatus((prev) => `Saved: ${result.name} (v${result.current_version})`)
    } catch (err: any) {
      setTestStatus(`Save failed: ${err.message}`)
    } finally {
      setIsSaving(false)
    }
  }

  const handleTestRun = async () => {
    setIsExecuting(true)
    setSignalOutput([])
    try {
      let strategyId = savedStrategyId

      // If strategy not yet saved, create it first
      if (!strategyId) {
        const graph = buildGraphPayload()
        const created = await api.strategies.create({
          name: strategyName,
          description: '',
          graph,
        })
        strategyId = created.id
        setSavedStrategyId(strategyId)
        markSaved()
      }

      // Execute the strategy
      const result = await api.strategies.execute(strategyId)

      const signals = result.signals || []
      if (signals.length === 0) {
        setTestStatus('No signals generated')
        setSignalOutput(['No BUY/SELL signals detected'])
      } else {
        setTestStatus(`Executed: ${signals.length} signal(s)`)
        setSignalOutput(
          signals.map(
            (s: any) =>
              `${s.action.toUpperCase()} (${s.node_id}) — ${JSON.stringify(s.params)}`
          )
        )
      }
    } catch (err: any) {
      setTestStatus(`Execution failed: ${err.message}`)
      setSignalOutput([`Error: ${err.message}`])
    } finally {
      setIsExecuting(false)
    }
  }

  const handleDeleteSelected = () => {
    if (!selectedNodeIds.length) return
    selectedNodeIds.forEach((id) => removeNode(id))
    setSelectedNodeIds([])
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'TEXTAREA'].includes(target.tagName)) return
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedNodeIds.length > 0) {
        event.preventDefault()
        handleDeleteSelected()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [selectedNodeIds])

  return (
    <div className="flex h-[calc(100vh-8rem)] rounded-xl border border-border overflow-hidden glass">
      <NodePalette onDragStart={onDragStart} />

      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center justify-between gap-4 p-4 border-b border-border">
          <div className="flex items-center gap-3 min-w-0">
            <Input
              value={strategyName}
              onChange={(e) => setStrategyName(e.target.value)}
              className="max-w-xs h-9 font-medium bg-transparent border-none text-base focus-visible:ring-0 px-0"
            />
            {isDirty && <Badge variant="warning">Unsaved</Badge>}
            <Badge variant="secondary">{testStatus}</Badge>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleTestRun}
              disabled={isExecuting || isSaving}
            >
              <Play className="h-4 w-4 mr-1" />
              {isExecuting ? 'Running...' : 'Test Run'}
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={handleDeleteSelected}
              disabled={!selectedNodeIds.length}
            >
              <Trash2 className="h-4 w-4 mr-1" />
              Delete Selected
            </Button>
            <Button size="sm" onClick={handleSave} disabled={isSaving}>
              <Save className="h-4 w-4" />
              {isSaving ? 'Saving...' : 'Save Strategy'}
            </Button>
          </div>
        </div>

        {/* Signal Output Panel */}
        {signalOutput.length > 0 && (
          <div className="border-b border-border bg-muted/30 p-3 max-h-32 overflow-y-auto">
            <div className="text-xs font-medium text-muted-foreground mb-1">Signal Output</div>
            {signalOutput.map((line, i) => (
              <div key={i} className="text-xs font-mono text-foreground">
                {line}
              </div>
            ))}
          </div>
        )}

        <div ref={reactFlowWrapper} className="flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onDragOver={onDragOver}
            onDrop={onDrop}
            onSelectionChange={({ nodes: selectedNodes }) => {
              const nextSelection = selectedNodes.map((node) => node.id)
              setSelectedNodeIds((current) => {
                const currentKey = current.join(',')
                const nextKey = nextSelection.join(',')
                return currentKey === nextKey ? current : nextSelection
              })
            }}
            nodeTypes={nodeTypes}
            fitView
            className="bg-background/50"
            defaultEdgeOptions={{ animated: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(148,163,184,0.1)" />
            <Controls />
            <MiniMap
              nodeColor={(n) => {
                const colors: Record<string, string> = {
                  rsi: '#22d3ee',
                  buy: '#10b981',
                  sell: '#ef4444',
                  condition: '#f59e0b',
                }
                return colors[n.type ?? ''] ?? '#6366f1'
              }}
              maskColor="rgba(6, 8, 15, 0.8)"
            />
          </ReactFlow>
        </div>
      </div>
    </div>
  )
}