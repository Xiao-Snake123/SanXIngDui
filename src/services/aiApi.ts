export interface SceneGenerationParams {
  identity: string
  scene: string
  item: string
  style: string
  negativePrompt?: string
  seed?: number
}

export interface GenerationProgress {
  progress: number
  message: string
}

type WorkflowLink = [number, number, number, number, number, string?]

interface WorkflowNodeInput {
  name: string
  type: string
  link?: number | null
  widget?: {
    name?: string
  }
}

interface WorkflowNode {
  id: number
  type: string
  title?: string
  inputs?: WorkflowNodeInput[]
  widgets_values?: unknown[]
  outputs?: Array<{ links?: number[] }>
}

interface WorkflowSubgraphLink {
  id: number
  origin_id: number
  origin_slot: number
  target_id: number
  target_slot: number
  type?: string
}

interface WorkflowSubgraphIO {
  name: string
  type?: string
  linkIds?: number[]
}

interface WorkflowSubgraph {
  id: string
  inputs?: WorkflowSubgraphIO[]
  outputs?: WorkflowSubgraphIO[]
  nodes?: WorkflowNode[]
  links?: WorkflowSubgraphLink[]
}

interface WorkflowDefinition {
  nodes: WorkflowNode[]
  links: WorkflowLink[]
  definitions?: {
    subgraphs?: WorkflowSubgraph[]
  }
}

interface ComfyImage {
  filename: string
  subfolder?: string
  type?: string
}

const COMFY_BASE = '/comfy'
const WORKFLOW_URL = '/workflows/Z-Image-Turbo%20.json'
const DEFAULT_NEGATIVE_PROMPT =
  'lowres, blurry, jpeg artifacts, watermark, text, logo, oversaturated, cartoon, anime, illustration, painting, cgi, 3d render, deformed, bad anatomy, extra limbs, extra fingers'
const NON_EXECUTABLE_NODE_TYPES = new Set(['Note', 'MarkdownNote'])

const WIDGET_ORDER: Record<string, string[]> = {
  CheckpointLoaderSimple: ['ckpt_name'],
  LoraLoader: ['lora_name', 'strength_model', 'strength_clip'],
  PrimitiveStringMultiline: ['value'],
  SXD_ZH2ENPrompt: ['ensure_triggers', 'add_photo_suffix'],
  SXD_ZH2ENPromptLLM: ['text', 'model_id', 'forced_trigger', 'trigger_keywords', 'auto_inject_trigger'],
  CLIPTextEncode: ['text'],
  EmptyLatentImage: ['width', 'height', 'batch_size'],
  KSampler: ['seed', 'control_after_generate', 'steps', 'cfg', 'sampler_name', 'scheduler', 'denoise'],
  SaveImage: ['filename_prefix'],
}

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function createPrompt(params: SceneGenerationParams): string {
  return [
    '三星堆文明',
    `文物主体：${params.item}`,
    `人物身份：${params.identity}`,
    `场景地点：${params.scene}`,
    `视觉风格：${params.style}`,
    '青铜材质、孔雀石绿色铜锈、自然氧化痕迹',
    '超高清、细节清晰、8K',
  ].join('，')
}

async function fetchWorkflowTemplate(): Promise<WorkflowDefinition> {
  const response = await fetch(WORKFLOW_URL, { cache: 'no-store' })
  if (!response.ok) {
    throw new Error('无法加载前端工作流模板')
  }

  return response.json()
}

function applySceneParams(workflow: WorkflowDefinition, params: SceneGenerationParams): WorkflowDefinition {
  const nextWorkflow = deepClone(workflow)
  const prompt = createPrompt(params)
  const seed = params.seed ?? Math.floor(Math.random() * 2147483647)
  const savePrefix = `sanxingdui_${Date.now()}`

  for (const node of nextWorkflow.nodes) {
    if (node.type === 'PrimitiveStringMultiline' || node.id === 9) {
      const widgets = [...(node.widgets_values || [])]
      widgets[0] = prompt
      node.widgets_values = widgets
    }

    if (node.id === 10) {
      node.widgets_values = [params.negativePrompt || DEFAULT_NEGATIVE_PROMPT]
    }

    if (node.id === 6 || node.type === 'KSampler') {
      const widgets = [...(node.widgets_values || [])]
      widgets[0] = seed
      widgets[1] = 'fixed'
      node.widgets_values = widgets
    }

    if (node.id === 8 || node.type === 'SaveImage') {
      node.widgets_values = [savePrefix]
    }
  }

  return nextWorkflow
}

function expandSubgraphInstances(workflow: WorkflowDefinition): WorkflowDefinition {
  const nextWorkflow = deepClone(workflow)
  const subgraphs = new Map((nextWorkflow.definitions?.subgraphs || []).map((item) => [item.id, item]))
  if (subgraphs.size === 0) {
    return nextWorkflow
  }

  let links: WorkflowLink[] = [...(nextWorkflow.links || [])]
  let maxNodeId = nextWorkflow.nodes.reduce((max, node) => Math.max(max, node.id), 0)
  let maxLinkId = links.reduce((max, link) => Math.max(max, link[0]), 0)

  const resultNodes: WorkflowNode[] = []

  for (const node of nextWorkflow.nodes) {
    const subgraph = subgraphs.get(node.type)
    if (!subgraph) {
      resultNodes.push(node)
      continue
    }

    const incomingLinks = links.filter((link) => link[3] === node.id)
    const outgoingLinks = links.filter((link) => link[1] === node.id)
    links = links.filter((link) => link[1] !== node.id && link[3] !== node.id)

    const idMap = new Map<number, number>()
    const clonedNodes: WorkflowNode[] = (subgraph.nodes || []).map((subNode) => {
      const newId = ++maxNodeId
      idMap.set(subNode.id, newId)
      return {
        ...deepClone(subNode),
        id: newId,
      }
    })

    const subgraphLinksById = new Map<number, WorkflowSubgraphLink>()
    for (const subLink of subgraph.links || []) {
      subgraphLinksById.set(subLink.id, subLink)
    }

    const internalLinkIdMap = new Map<number, number>()
    for (const subLink of subgraph.links || []) {
      if (subLink.origin_id < 0 || subLink.target_id < 0) {
        continue
      }
      const originId = idMap.get(subLink.origin_id)
      const targetId = idMap.get(subLink.target_id)
      if (originId == null || targetId == null) {
        continue
      }

      const newLinkId = ++maxLinkId
      internalLinkIdMap.set(subLink.id, newLinkId)
      links.push([newLinkId, originId, subLink.origin_slot, targetId, subLink.target_slot, subLink.type])
    }

    for (const clonedNode of clonedNodes) {
      for (const input of clonedNode.inputs || []) {
        if (typeof input.link === 'number') {
          input.link = internalLinkIdMap.get(input.link) ?? null
        }
      }
      for (const output of clonedNode.outputs || []) {
        if (Array.isArray(output.links)) {
          output.links = output.links
            .map((oldLinkId) => internalLinkIdMap.get(oldLinkId))
            .filter((value): value is number => typeof value === 'number')
        }
      }
    }

    for (const subInput of subgraph.inputs || []) {
      const instanceInput = node.inputs?.find((input) => input.name === subInput.name)
      const externalIncoming =
        typeof instanceInput?.link === 'number'
          ? incomingLinks.find((link) => link[0] === instanceInput.link)
          : undefined

      if (!externalIncoming) {
        continue
      }

      for (const linkId of subInput.linkIds || []) {
        const subLink = subgraphLinksById.get(linkId)
        if (!subLink || subLink.target_id < 0) {
          continue
        }
        const targetId = idMap.get(subLink.target_id)
        if (targetId == null) {
          continue
        }

        const newLinkId = ++maxLinkId
        links.push([
          newLinkId,
          externalIncoming[1],
          externalIncoming[2],
          targetId,
          subLink.target_slot,
          subInput.type || externalIncoming[5],
        ])
      }
    }

    for (let outputSlot = 0; outputSlot < (subgraph.outputs || []).length; outputSlot += 1) {
      const subOutput = subgraph.outputs?.[outputSlot]
      if (!subOutput) {
        continue
      }

      let sourceNodeId: number | null = null
      let sourceSlot: number | null = null
      let sourceType: string | undefined

      for (const linkId of subOutput.linkIds || []) {
        const subLink = subgraphLinksById.get(linkId)
        if (!subLink || subLink.origin_id < 0) {
          continue
        }
        const mappedOrigin = idMap.get(subLink.origin_id)
        if (mappedOrigin == null) {
          continue
        }
        sourceNodeId = mappedOrigin
        sourceSlot = subLink.origin_slot
        sourceType = subLink.type
        break
      }

      if (sourceNodeId == null || sourceSlot == null) {
        continue
      }

      for (const externalOutgoing of outgoingLinks.filter((link) => link[2] === outputSlot)) {
        const newLinkId = ++maxLinkId
        links.push([
          newLinkId,
          sourceNodeId,
          sourceSlot,
          externalOutgoing[3],
          externalOutgoing[4],
          externalOutgoing[5] || sourceType || subOutput.type,
        ])
      }
    }

    resultNodes.push(...clonedNodes)
  }

  const targetLinkByInput = new Map<string, number>()
  for (const link of links) {
    targetLinkByInput.set(`${link[3]}:${link[4]}`, link[0])
  }

  for (const node of resultNodes) {
    node.inputs = (node.inputs || []).map((input, index) => {
      const matched = targetLinkByInput.get(`${node.id}:${index}`)
      return {
        ...input,
        link: matched ?? null,
      }
    })
  }

  nextWorkflow.nodes = resultNodes
  nextWorkflow.links = links
  return nextWorkflow
}

function stripUiOnlyNodes(workflow: WorkflowDefinition): WorkflowDefinition {
  const nextWorkflow = deepClone(workflow)
  const removedNodeIds = new Set(
    nextWorkflow.nodes
      .filter((node) => NON_EXECUTABLE_NODE_TYPES.has(node.type))
      .map((node) => node.id),
  )

  if (removedNodeIds.size === 0) {
    return nextWorkflow
  }

  nextWorkflow.nodes = nextWorkflow.nodes.filter((node) => !removedNodeIds.has(node.id))
  nextWorkflow.links = nextWorkflow.links.filter(
    (link) => !removedNodeIds.has(link[1]) && !removedNodeIds.has(link[3]),
  )

  const activeLinkIds = new Set(nextWorkflow.links.map((link) => link[0]))
  for (const node of nextWorkflow.nodes) {
    for (const input of node.inputs || []) {
      if (typeof input.link === 'number' && !activeLinkIds.has(input.link)) {
        input.link = null
      }
    }
  }

  return nextWorkflow
}

function stripUnresolvedSubgraphNodes(workflow: WorkflowDefinition): WorkflowDefinition {
  const nextWorkflow = deepClone(workflow)
  const uuidLike = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

  const removedNodeIds = new Set(
    nextWorkflow.nodes.filter((node) => uuidLike.test(node.type)).map((node) => node.id),
  )

  if (removedNodeIds.size === 0) {
    return nextWorkflow
  }

  nextWorkflow.nodes = nextWorkflow.nodes.filter((node) => !removedNodeIds.has(node.id))
  nextWorkflow.links = nextWorkflow.links.filter(
    (link) => !removedNodeIds.has(link[1]) && !removedNodeIds.has(link[3]),
  )

  const activeLinkIds = new Set(nextWorkflow.links.map((link) => link[0]))
  for (const node of nextWorkflow.nodes) {
    for (const input of node.inputs || []) {
      if (typeof input.link === 'number' && !activeLinkIds.has(input.link)) {
        input.link = null
      }
    }
  }

  const imageInputLinked = (node: WorkflowNode) =>
    (node.inputs || []).some((input) => input.name === 'images' && typeof input.link === 'number')

  const invalidOutputNodeIds = new Set(
    nextWorkflow.nodes
      .filter((node) => node.type === 'SaveImage' && !imageInputLinked(node))
      .map((node) => node.id),
  )

  if (invalidOutputNodeIds.size > 0) {
    nextWorkflow.nodes = nextWorkflow.nodes.filter((node) => !invalidOutputNodeIds.has(node.id))
    nextWorkflow.links = nextWorkflow.links.filter(
      (link) => !invalidOutputNodeIds.has(link[1]) && !invalidOutputNodeIds.has(link[3]),
    )
  }

  return nextWorkflow
}

function normalizeWorkflowForApi(workflow: WorkflowDefinition): WorkflowDefinition {
  const nextWorkflow = deepClone(workflow)

  const translatorNodes = nextWorkflow.nodes.filter((node) => node.type === 'SXD_ZH2ENPrompt')
  for (const translator of translatorNodes) {
    const widgetCount = translator.widgets_values?.length ?? 0
    if (widgetCount >= 6) {
      continue
    }

    const textInputLinkId = translator.inputs?.find((input) => input.name === 'text')?.link
    if (typeof textInputLinkId !== 'number') {
      continue
    }

    const sourceLink = nextWorkflow.links.find((link) => link[0] === textInputLinkId)
    if (!sourceLink) {
      continue
    }

    for (const link of nextWorkflow.links) {
      if (link[1] === translator.id) {
        link[1] = sourceLink[1]
        link[2] = sourceLink[2]
      }
    }

    nextWorkflow.nodes = nextWorkflow.nodes.filter((node) => node.id !== translator.id)
  }

  return nextWorkflow
}

function convertWorkflowToApi(workflow: WorkflowDefinition) {
  const linkMap = new Map<number, [string, number]>()
  for (const link of workflow.links || []) {
    linkMap.set(link[0], [String(link[1]), Number(link[2])])
  }

  const prompt: Record<string, { class_type: string; inputs: Record<string, unknown> }> = {}

  for (const node of workflow.nodes || []) {
    if (NON_EXECUTABLE_NODE_TYPES.has(node.type)) {
      continue
    }

    const nodeId = String(node.id)
    const inputs: Record<string, unknown> = {}

    for (const input of node.inputs || []) {
      if (input.link == null) {
        continue
      }

      const source = linkMap.get(input.link)
      if (source) {
        inputs[input.name] = source
      }
    }

    const widgetValues = node.widgets_values || []
    const widgetNames = WIDGET_ORDER[node.type] || []

    if (widgetNames.length > 0) {
      widgetNames.forEach((name, index) => {
        if (index >= widgetValues.length) {
          return
        }

        if (name in inputs) {
          return
        }

        inputs[name] = widgetValues[index]
      })
    } else {
      const widgetBackedInputs = (node.inputs || []).filter((input) => {
        const widgetName = input.widget?.name
        return typeof widgetName === 'string' && widgetName.length > 0
      })

      widgetBackedInputs.forEach((input, index) => {
        if (index >= widgetValues.length) {
          return
        }

        if (input.name in inputs) {
          return
        }

        const value = widgetValues[index]
        if (value !== undefined) {
          inputs[input.name] = value
        }
      })
    }

    prompt[nodeId] = {
      class_type: node.type,
      inputs,
    }
  }

  return prompt
}

function buildImageUrl(image: ComfyImage): string {
  const params = new URLSearchParams({
    filename: image.filename,
    subfolder: image.subfolder || '',
    type: image.type || 'output',
    _: String(Date.now()),
  })

  return `${COMFY_BASE}/view?${params.toString()}`
}

function extractImageUrl(history: Record<string, any>, promptId: string): string | null {
  const record = history?.[promptId]
  const outputs = record?.outputs || {}

  for (const nodeOutput of Object.values(outputs) as Array<{ images?: ComfyImage[] }>) {
    const image = nodeOutput?.images?.[0]
    if (image?.filename) {
      return buildImageUrl(image)
    }
  }

  const statusMessage = record?.status?.messages?.find?.((entry: any) => entry?.[0] === 'execution_error')
  if (statusMessage?.[1]?.exception_message) {
    throw new Error(statusMessage[1].exception_message)
  }

  return null
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export async function generateSceneImage(
  params: SceneGenerationParams,
  onProgress?: (progress: GenerationProgress) => void,
): Promise<string> {
  onProgress?.({ progress: 8, message: '正在加载工作流...' })
  const template = await fetchWorkflowTemplate()
  const workflow = normalizeWorkflowForApi(
    stripUnresolvedSubgraphNodes(stripUiOnlyNodes(expandSubgraphInstances(applySceneParams(template, params)))),
  )
  const prompt = convertWorkflowToApi(workflow)

  onProgress?.({ progress: 18, message: '正在提交到 ComfyUI...' })
  const submitResponse = await fetch(`${COMFY_BASE}/prompt`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      client_id: globalThis.crypto?.randomUUID?.() || `web-${Date.now()}`,
      prompt,
    }),
  })

  if (!submitResponse.ok) {
    const errorText = await submitResponse.text()
    throw new Error(`提交到 ComfyUI 失败: ${errorText || submitResponse.status}`)
  }

  const submitData = await submitResponse.json()
  if (!submitData.prompt_id) {
    throw new Error('ComfyUI 未返回 prompt_id')
  }

  if (submitData.node_errors && Object.keys(submitData.node_errors).length > 0) {
    throw new Error('工作流校验失败，请检查 ComfyUI 节点配置')
  }

  const promptId = submitData.prompt_id as string
  const startTime = Date.now()
  const timeout = 10 * 60 * 1000

  onProgress?.({ progress: 28, message: '任务已进入队列...' })

  while (Date.now() - startTime < timeout) {
    await sleep(1500)

    const elapsed = Date.now() - startTime
    const progress = Math.min(92, 28 + (elapsed / timeout) * 64)
    onProgress?.({ progress, message: '正在生成图像...' })

    const historyResponse = await fetch(`${COMFY_BASE}/history/${promptId}`, {
      cache: 'no-store',
    })

    if (!historyResponse.ok) {
      continue
    }

    const history = await historyResponse.json()
    const imageUrl = extractImageUrl(history, promptId)
    if (imageUrl) {
      onProgress?.({ progress: 100, message: '生成完成' })
      return imageUrl
    }
  }

  throw new Error('等待 ComfyUI 生成超时')
}

export async function checkComfyUIStatus(): Promise<boolean> {
  try {
    const response = await fetch(`${COMFY_BASE}/system_stats`, { cache: 'no-store' })
    return response.ok
  } catch {
    return false
  }
}

export function getScenePromptPreview(params: SceneGenerationParams): string {
  return createPrompt(params)
}
