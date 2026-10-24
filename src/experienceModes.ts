// 2026-10-03 교수님 피드백: 채팅은 빼고 출력물(웹진·이야기·영상)을 앞에, 도표·분석 데이터는 뒷단으로.
// 채팅 엔진(storyseed-talk.html)은 파일로 남겨 두고 메뉴에서만 뺐다.
export type ExperienceModeId = 'webzine' | 'novel' | 'visual' | 'insight'

export type ExperienceMode = {
  id: ExperienceModeId
  index: string
  label: string
  koLabel: string
  description: string
  document?: string
}

export const experienceModes: ExperienceMode[] = [
  {
    id: 'webzine',
    index: '01',
    label: 'WEBZINE',
    koLabel: '웹진',
    description: '일주일 기록으로 웹진 한 호를 만듭니다.',
  },
  {
    id: 'novel',
    index: '02',
    label: 'NOVEL',
    koLabel: '노벨',
    description: '기록에서 이야기를 짓습니다.',
    document: 'storyseed-novel.html',
  },
  {
    id: 'visual',
    index: '03',
    label: 'VISUAL',
    koLabel: '비주얼',
    description: '기록의 잠재공간을 봅니다.',
  },
  {
    id: 'insight',
    index: '04',
    label: 'DATA',
    koLabel: '분석',
    description: '웹진과 이야기 뒤의 계산을 봅니다.',
  },
]
