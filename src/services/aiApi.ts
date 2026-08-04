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

interface DashScopeContentItem {
  text?: string
  image?: string
}

interface DashScopeResponse {
  output?: {
    choices?: Array<{
      message?: {
        content?: DashScopeContentItem[]
      }
    }>
  }
  code?: string
  message?: string
  request_id?: string
}

const DASHSCOPE_API_KEY = import.meta.env.VITE_DASHSCOPE_API_KEY || ''
const DASHSCOPE_GENERATION_URL = DASHSCOPE_API_KEY
  ? 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
  : '/dashscope/api/v1/services/aigc/multimodal-generation/generation'
const DEFAULT_NEGATIVE_PROMPT =
  'lowres, blurry, jpeg artifacts, watermark, text, logo, oversaturated, cartoon, anime, illustration, painting, cgi, 3d render, deformed, bad anatomy, extra limbs, extra fingers'

function createPrompt(params: SceneGenerationParams): string {
  const negativePrompt = params.negativePrompt || DEFAULT_NEGATIVE_PROMPT

  return [
    `我希望复原一个三星堆文明的场景，其中主要的文物是——${params.item}，身份为${params.identity}，站在${params.scene}前，以${params.style}风格呈现`,
    '三星堆文物自然千年氧化斑驳痕迹，肌理清晰，器物雕刻纹路精致',
    '8K超高清，RAW摄影画质，超高细节，微距质感，青铜器凹凸纹理清晰',
    '写实纪实摄影，自然光影，镜头无畸变，色彩真实，文物原样忠实还原',
  ].join('。')
}

function extractDashScopeImageUrl(data: DashScopeResponse): string | null {
  const content = data.output?.choices?.[0]?.message?.content || []
  const imageItem = content.find((item) => typeof item.image === 'string' && item.image.length > 0)

  return imageItem?.image || null
}

export async function generateSceneImage(
  params: SceneGenerationParams,
  onProgress?: (progress: GenerationProgress) => void,
): Promise<string> {
  const prompt = createPrompt(params)
  const parameters: Record<string, boolean | number | string> = {
    prompt_extend: false,
    size: '1120*1440',
  }

  if (typeof params.seed === 'number') {
    parameters.seed = params.seed
  }

  onProgress?.({ progress: 15, message: '正在提交到通义万相...' })
  const response = await fetch(DASHSCOPE_GENERATION_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(DASHSCOPE_API_KEY ? { Authorization: `Bearer ${DASHSCOPE_API_KEY}` } : {}),
    },
    body: JSON.stringify({
      model: 'z-image-turbo',
      input: {
        messages: [
          {
            role: 'user',
            content: [
              {
                text: prompt,
              },
            ],
          },
        ],
      },
      parameters,
    }),
  })

  onProgress?.({ progress: 75, message: '正在生成图像...' })

  const responseText = await response.text()
  let data: DashScopeResponse

  try {
    data = JSON.parse(responseText) as DashScopeResponse
  } catch {
    throw new Error(`通义万相返回了无法解析的响应: ${responseText || response.status}`)
  }

  if (!response.ok) {
    throw new Error(data.message || data.code || `通义万相请求失败: ${response.status}`)
  }

  const imageUrl = extractDashScopeImageUrl(data)
  if (!imageUrl) {
    throw new Error(data.message || '通义万相未返回图片地址')
  }

  onProgress?.({ progress: 100, message: '生成完成' })
  return imageUrl
}

export async function checkDashScopeStatus(): Promise<boolean> {
  try {
    const response = await fetch(DASHSCOPE_GENERATION_URL, {
      method: 'OPTIONS',
    })
    return response.ok || response.status === 204 || response.status === 405
  } catch {
    return false
  }
}

export const checkComfyUIStatus = checkDashScopeStatus

export function getScenePromptPreview(params: SceneGenerationParams): string {
  return createPrompt(params)
}
