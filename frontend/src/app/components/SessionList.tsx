import { Plus, ShieldOff, Trash2 } from "lucide-react";
import type { ChatSessionSummary } from "@/services/agentApi";

const GOLD = "#D4AF37";
const MUTED = "#556372";
const BG = "#0D1117";

interface SessionListProps {
  sessions: ChatSessionSummary[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  /** 清除记忆：删掉全部会话记录 + 长期画像（「这个人是谁」）。 */
  onClearMemory: () => void;
}

function formatTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

/**
 * 历史会话列表。
 *
 * 为什么需要它：会话此前只活在 React state 与 Redis 缓存里——
 * 刷新页面就失联，30 分钟后 Redis 过期更是彻底找不到。
 * 这里的数据来自后端**持久记录**（PostgreSQL），刷新、隔天回来都还在，
 * 点开还能继续聊（服务端会按需从记录回温会话）。
 */
export function SessionList({
  sessions,
  currentId,
  onSelect,
  onNew,
  onDelete,
  onClearMemory,
}: SessionListProps) {
  return (
    <aside
      style={{
        width: 220,
        flexShrink: 0,
        borderRight: `1px solid ${GOLD}20`,
        background: BG,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <div style={{ padding: "10px 12px", display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: GOLD, letterSpacing: 1 }}>会话记录</span>
        <button
          type="button"
          onClick={onNew}
          title="新建会话"
          style={{
            marginLeft: "auto",
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "3px 8px",
            fontSize: 12,
            color: GOLD,
            background: `${GOLD}14`,
            border: `1px solid ${GOLD}40`,
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          <Plus size={12} /> 新建
        </button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "0 8px 8px" }}>
        {sessions.length === 0 && (
          <div style={{ padding: "8px 4px", fontSize: 12, color: MUTED }}>还没有历史会话</div>
        )}
        {sessions.map((item) => {
          const active = item.session_id === currentId;
          return (
            <div
              key={item.session_id}
              onClick={() => onSelect(item.session_id)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "8px",
                marginBottom: 4,
                borderRadius: 6,
                cursor: "pointer",
                background: active ? `${GOLD}1A` : "transparent",
                borderLeft: active ? `2px solid ${GOLD}` : "2px solid transparent",
              }}
            >
              <div style={{ minWidth: 0, flex: 1 }}>
                <div
                  style={{
                    fontSize: 12,
                    color: active ? GOLD : "#C9D1D9",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                  title={item.title ?? ""}
                >
                  {item.title || "（无标题）"}
                </div>
                <div style={{ fontSize: 11, color: MUTED, marginTop: 2 }}>
                  {item.turn_count} 轮 · {formatTime(item.last_message_at)}
                </div>
              </div>
              <button
                type="button"
                title="删除会话"
                onClick={(event) => {
                  // 阻止冒泡：否则点删除会顺带触发「打开该会话」
                  event.stopPropagation();
                  onDelete(item.session_id);
                }}
                style={{
                  background: "transparent",
                  border: "none",
                  color: MUTED,
                  cursor: "pointer",
                  padding: 2,
                  display: "flex",
                  flexShrink: 0,
                }}
              >
                <Trash2 size={13} />
              </button>
            </div>
          );
        })}
      </div>

      <div style={{ padding: 8, borderTop: `1px solid ${GOLD}20` }}>
        <button
          type="button"
          title="删除全部会话记录，以及 AI 对你的了解（名字、偏好）"
          onClick={() => {
            // 二次确认：这是不可撤销的操作，误点一下不该清掉全部历史
            const ok = window.confirm(
              "清除记忆将删除：全部会话记录 + AI 对你的了解（名字、偏好）。此操作无法恢复，确定继续？",
            );
            if (ok) onClearMemory();
          }}
          style={{
            width: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 6,
            padding: "6px 8px",
            fontSize: 12,
            color: MUTED,
            background: "transparent",
            border: `1px solid ${GOLD}25`,
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          <ShieldOff size={12} /> 清除记忆
        </button>
      </div>
    </aside>
  );
}
