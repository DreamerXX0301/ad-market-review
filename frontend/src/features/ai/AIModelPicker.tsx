import { providerModelChoices } from "./aiState";
import type { AIProvider } from "./aiTypes";

export default function AIModelPicker({
  provider,
  value,
  onChange,
}: {
  provider: AIProvider;
  value: string;
  onChange: (value: string) => void;
}) {
  const choices = providerModelChoices(provider);
  const fixed = provider.provider_id === "openai";
  const listed = choices.some((choice) => choice.id === value);
  return (
    <>
      <label>
        常用模型
        <select
          value={listed ? value : fixed ? "" : "custom"}
          onChange={(event) =>
            onChange(event.target.value === "custom" ? "" : event.target.value)
          }
        >
          {fixed && !listed && (
            <option value="" disabled>
              请选择常用模型
            </option>
          )}
          {choices.map((choice) => (
            <option key={choice.id} value={choice.id}>
              {choice.name}
            </option>
          ))}
          {!fixed && <option value="custom">填写其他模型 ID</option>}
        </select>
      </label>
      {fixed ? (
        <p className="muted">
          {listed
            ? "切换模型后须保存并验证，账号可用性以实际验证结果为准。"
            : `当前保存的模型 ${value || "尚未选择"} 不在常用列表中；原配置与历史仍保留，请选择常用模型后重新验证。`}
        </p>
      ) : (
        <label>
          实际模型 ID
          <input
            value={value}
            maxLength={100}
            spellCheck={false}
            onChange={(event) => onChange(event.target.value)}
            required
            pattern="[A-Za-z0-9][A-Za-z0-9_.:\\-]{0,99}"
          />
        </label>
      )}
    </>
  );
}
