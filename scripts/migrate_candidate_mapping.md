# Flat cache model → HuggingFace repo (candidate mapping)

Source: HF search candidates embedded in `scripts/migrate_known_repos.json`. `best_match` is the top candidate by keyword overlap with the filename; **not basename-verified**. mmproj rows are paired to their base model where the name makes it obvious.

| Cache file | Best-match repo | Confidence | Alternatives |
|---|---|---|---|
| `Qwen3.5-9B-Claude-4.6-OS-AV-H-UNCENSORED-THINK-D_AU-Q8_0.gguf` | mmp2055/qwen3.5-9b-claude-4.6-os-av-h-uncensored-think-d_au-imat-bughunter-v6 | high | mmp2055/Qwen3.5-9B-Claude-4.6-OS-AV-H-UNCENSORED-THINK-D_AU-Q4_K_S-imat-bughunter |
| `Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q6_K.gguf` | Daxin/Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q4_K_S | high |  |
| `Qwen3.6-40B-Deck-Opus-NEO-CODE-HERE-2T-OT-HIGH-Q8_0.gguf` | sahu-ai-research/Qwen3.6-40B-Deck-Opus-NEO-CODE-HERE-2T-OT-HIGH-MTP-SAHU-Q8_0 | high | MarkZuckerCrack/Qwen3.6-40B-Deck-Opus-NEO-CODE-HERE-2T-OT-HIGH-Q8_0 |
| `Qwen3.8-27B-Cold-Fusion-GAIN-V1.1-NM-DAU-NEO-MAX-NEO-MTP-Q4_K_M.gguf` | developerjeremylive/Qwen3.8-27B-Cold-Fusion-GAIN-V1.1-NM-DAU-NEO-MAX-MTP-GGUF-etheroi | high | DavidAU/Qwen3.8-27B-Cold-Fusion-GAIN-V1.1-NM-DAU-NEO-MAX-MTP-GGUF |
| `Qwen3.8-27B-Cold-Fusion-GAIN-V1.1-NM-DAU-NEO-MAX-NEO-MTP-Q8_0.gguf` | developerjeremylive/Qwen3.8-27B-Cold-Fusion-GAIN-V1.1-NM-DAU-NEO-MAX-MTP-GGUF-etheroi | high | DavidAU/Qwen3.8-27B-Cold-Fusion-GAIN-V1.1-NM-DAU-NEO-MAX-MTP-GGUF |
| `Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-Q6_K.gguf` | Daxin/Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q4_K_S | high |  |
| `Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf` | Vincentt/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF-Q4_K_P | high | taurusduan/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF, nurdich/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF, lstari/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF |
| `Phi-3.5-mini-instruct-Q4_K_M.gguf` | pprp/Phi-3.5-mini-instruct-Q4_K_M-GGUF | high | mrmage/Phi-3.5-mini-instruct-Q4_K_M-GGUF, goodasdgood/Phi-3.5-mini-instruct-Q4_K_M-GGUF, garrofe/Phi-3.5-mini-instruct-Q4_K_M-GGUF |
| `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` | meshllm/Qwen3.6-35B-A3B-UD-Q4_K_XL-layers | high | XpressAI/Qwen3.6-35B-A3B-ExpertClone-Logic-UD-Q4_K_XL-GGUF, LucianoM39/Qwen3.6-35B-A3B-UD-Q4_K_XL-Mesh-LLM, juan1995-dev/Qwen3.6-35B-A3B-UD-Q4_K_M_GGUF |
| `Muse-Glimmer-30B-UD-Q4_K_XL.gguf` | lancejames221b/Muse-Glimmer-30B-UD-Q4_K_XL-262k-patch | high |  |
| `Qwen-AgentWorld-35B-A3B-UD-Q8_K_XL.gguf` | vadikulous-ai/Qwen-AgentWorld-35B-A3B-UD-Q4_K_XL-MTP-GGUF | high | meshllm/Qwen-AgentWorld-35B-A3B-UD-Q4_K_XL-layers, coolthor/Huihui-Qwen-AgentWorld-35B-A3B-abliterated-UD-Q3_K_M-GGUF |
| `Qwen3-0.6B-Base.Q4_K_M.gguf` | itlwas/Qwen3-0.6B-Base-Q4_K_M-GGUF | high | elichen-skymizer/Qwen3-0.6B-Base-Q4_K_M-GGUF, devmasa/Qwen3-0.6B-Base-Q4_K_M-GGUF, YeonwooSung/Qwen3-0.6B-Base-Q4_K_M-GGUF |
| `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` | yuko29hu/Qwen3-4B-Instruct-2507-Q4_K_M-GGUF | high | mahdisml/Huihui-Qwen3-4B-Instruct-2507-abliterated-Q4_K_M-GGUF, fengpeisheng1/Josiefied-Qwen3-4B-Instruct-2507-gabliterated-v1-Q4_K_M-GGUF, enacimie/Qwen3-4B-Instruct-2507-Q4_K_M-GGUF |
| `Qwen3.5-4B-UD-Q4_K_XL.gguf` | XpressAI/Qwen3.5-4B-RYS-UD-Q4_K_XL-GGUF | high | Manojb/Qwen3.5-4B-UD-Q4_K_XL.gguf, Manojb/Qwen3.5-4B-UD-Q8_K_XL.gguf, Manojb/Qwen3.5-4B-UD-Q6_K_XL.gguf |
| `Qwen3.5-9B-UD-Q6_K_XL.gguf` | Manojb/Qwen3.5-9B-UD-Q6_K_XL.gguf | high | Manojb/Qwen3.5-9B-UD-Q8_K_XL.gguf, Manojb/Qwen3.5-9B-UD-Q5_K_XL.gguf, Manojb/Qwen3.5-9B-UD-Q4_K_XL.gguf |
| `Qwen3.6-27B-UD-Q4_K_XL.gguf` | yogthos/atlas-asa-qwen3.6-27b-mtp-ud-q4_k_xl | high | meshllm/Qwen3.6-27B-UD-Q4_K_XL-layers, YuYu1015/Huihui-Qwen3.6-27B-abliterated-UD-Q4_K_XL-MTP-GGUF, DAXZEIT/Qwen3.6-27B-Claude-Opus-Reasoning-UD-Q4_K_RA-XL-gguf |
| `Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf` | XpressAI/Qwen3.6-35B-A3B-ExpertClone-Logic-UD-Q4_K_XL-GGUF | high | juan1995-dev/Qwen3.6-35B-A3B-UD-Q5_K_M_GGUF, juan1995-dev/Qwen3.6-35B-A3B-UD-Q4_K_M_GGUF, DanyDA/unsloth_Qwen3.6-35B-A3B-UD-Q4_K_M-GGUF-SPLIT |
| `Qwen3.8-27B-UD-Q4_K_XL.gguf` | oolfBER/Qwen3.8-27B-UD-Q4_K_XL-single-GGUF | high | grimoni/Qwen3.8-27B-SSMFIX-UD-Q4_K_XL-GGUF, chimingw/qwen3.8-27b-ud-q5-k-xl-llamafile, Luis23333/Qwen3.8-27B-SSMFIX-UD-Q3_K_XL-GGUF |
| `Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q8_K_P.gguf` | AIconjured/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF-Q8-NVFP4 | high | taurusduan/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF, nurdich/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF, lstari/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF |
| `Kwaipilot_KAT-Coder-V2.5-Dev-Q6_K_L.gguf` | thread13/Kwaipilot_KAT-Coder-V2.5-Dev-GGUF-MTP | high | bartowski/Kwaipilot_KAT-Coder-V2.5-Dev-GGUF |
| `North-Mini-Code-1.0-Q6_K_L.gguf` | AdmiralGloom/North-Mini-Code-1.0-GGUF-Q6 | high | unsloth/North-Mini-Code-1.0-GGUF, michaelw9999/North-Mini-Code-1.0-NVFP4, michaelw9999/North-Mini-Code-1.0-GGUF |
| `Qwen3-14B-UD-Q4_K_XL.gguf` | meshllm/Qwen3-14B-UD-Q4_K_XL-layers | high |  |
| `Qwen3.8-27B-UD-Q6_K_XL.gguf` | oolfBER/Qwen3.8-27B-UD-Q4_K_XL-single-GGUF | high | chimingw/qwen3.8-27b-ud-q5-k-xl-llamafile, Luis23333/Qwen3.8-27B-SSMFIX-UD-Q3_K_XL-GGUF, robbintt/qwen3.8-27b-ud-iq2xxs-q8head |
| `Qwen3.8-27B-UD-Q8_K_XL.gguf` | oolfBER/Qwen3.8-27B-UD-Q4_K_XL-single-GGUF | high | chimingw/qwen3.8-27b-ud-q5-k-xl-llamafile, Luis23333/Qwen3.8-27B-SSMFIX-UD-Q3_K_XL-GGUF, robbintt/qwen3.8-27b-ud-iq2xxs-q8head |
| `Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q6_K_P.gguf` | taurusduan/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF | high | nurdich/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF, lstari/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF, IMUGLYHUH/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF |
| `ThinkingCap-Qwen3.6-27B-Q6_K.gguf` | gopi87/ThinkingCap-Qwen3.6-27B-Q6_K-GGUF | high | Abiray/ThinkingCap-Qwen3.6-27B-Q4_K_M-GGUF, Abiray/ThinkingCap-Qwen3.6-27B-MTP-Q4_K_M-GGUF, protoLabsAI/ThinkingCap-Qwen3.6-27B-MTP-GGUF |
| `ibm-granite_granite-4.1-3b-Q4_K_M.gguf` | mradermacher/IBM_granite-4.1-3b_Abliterated-i1-GGUF | high | mradermacher/IBM_granite-4.1-3b_Abliterated-GGUF, ibm-granite/granite-4.1-3b-fp8, ibm-granite/granite-4.1-3b-GGUF |
| `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` | unsloth/Qwen3.6-35B-A3B-MTP-GGUF | medium | unsloth/Qwen3.6-35B-A3B-GGUF, peculiar-ragdoll/Nail-Qwen3.6-35B-A3B-GGUF-MTP, SC117/Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved-APEX-GGUF |
| `ThinkingCap-Qwen3.6-27B-Q8_0.gguf` | protoLabsAI/ThinkingCap-Qwen3.6-27B-MTP-GGUF | medium | plunderstruck/ThinkingCap-Qwen3.6-27B-MTP-ROCmFP4-GGUF, gopi87/ThinkingCap-Qwen3.6-27B-Q6_K-GGUF, bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF |
| `diffusiongemma-26B-A4B-it-Q8_0.gguf` | unsloth/diffusiongemma-26B-A4B-it-GGUF | medium | meshllm/diffusiongemma-26B-A4B-it-Q4_K_M-layers, corsairnui/diffusiongemma-26b-a4b-it-strix-halo-fp16, Brunobkr/OFFELLIA_MXFP4_MOE_diffusiongemma-26B-A4B-it.gguf |
| `Qwythos-9B-v2-Q8_0.gguf` | shivamnaik/Qwythos-9B-v2-GGUF | medium | pressatojump/qwythos-9b-axiom-phase-b-v2, mradermacher/Qwythos-9B-v2-i1-GGUF, empero-ai/Qwythos-9B-v2-GGUF |
| `Bonsai-27B-Q1_0.gguf` | vinpix/Ternary-Bonsai-27B-Stock-MTP-GGUF | low | prism-ml/Ternary-Bonsai-27B-gguf, prism-ml/Bonsai-27B-gguf, livadies/Bonsai-27B-Android-Local |
| `Kwaipilot_KAT-Coder-V2.5-Dev-MTP-UD-Q5_K_XL.gguf` | None | ⚠ none / gated | _no candidates_ |
| `Lance_3B_Video-Q8_0.gguf` | None | ⚠ none / gated | _no candidates_ |
| `Ling-3.0-tiny-Q8_0.gguf` | None | ⚠ none / gated | _no candidates_ |
| `Ministral-3-3B-Instruct-2512-UD-Q4_K_XL.gguf` | None | ⚠ none / gated | _no candidates_ |
| `Ornith-1.0-9B-MTP-Q8_0.gguf` | None | ⚠ none / gated | _no candidates_ |
| `Ornith-1.5-35B-A3B-Q6_K_L.gguf` | None | ⚠ none / gated | _no candidates_ |
| `Ornith-1.5-9B-Q4_K_M.gguf` | None | ⚠ throttled — verify | ornith-ai/Ornith-1.5-9B-GGUF, AtomicChat/Ornith-1.5-9B-GGUF |
| `Ornith-1.5-9B-Q6_K.gguf` | None | ⚠ throttled — verify | ornith-ai/Ornith-1.5-9B-GGUF, AtomicChat/Ornith-1.5-9B-GGUF |
| `Qwen3.6-27B-NEO-CODE-2T-OT-Q6_K.gguf` | None | ⚠ none / gated | _no candidates_ |
| `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-Q6_K.gguf` | None | ⚠ none / gated | _no candidates_ |
| `Qwen3.8-27B-Cold-Fusion-GAIN-V1.1-NM-DAU-NEO-MAX-NEO-LOW-MTP-Q6_K.gguf` | None | ⚠ none / gated | _no candidates_ |
| `VibeThinker-3B-heretic.i1-Q6_K.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-BF16-Qwen3.6-27b.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-BF16-Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-BF16-gemma-4-12b.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-BF16-gemma-4-31B-it-qat-UD-Q4_K_XL.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-BF16.gguf` | juan1995-dev/Qwen3.6-27B-UD-Q5_K_L-mmproj-BF16-GGUF | low | juan1995-dev/Qwen3.5-35B-A3B-mmproj-BF16-GGUF, Thireus/mmproj-Qwen3.6-35B-A3B-THIREUS-BF16-SPECIAL_SPLIT, Thireus/mmproj-Qwen3.5-2B-THIREUS-BF16-SPECIAL_SPLIT |
| `mmproj-F16-Qwen3.6-27b.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-F16-Qwen3.6-35B-A3B-MTP.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-F16-Qwen3.8-27B-Cold-Fusion-GAIN-V1.1-NM-DAU-NEO-MAX-MTP.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-F16-Qwen3.8-27B.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-MUSE-GLIMMER-30B-BF16-Muse-Glimmer-30B.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-NEX-AGI_NEX-N2-MINI-BF16-nex-agi_Nex-N2-mini.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-ORNITH-1.5-35B-A3B-BF16-Ornith-1.5-35B-A3B.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-ORNITH-1.5-9B-BF16-Ornith-1.5-9B.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-QWEN3.8-27B-UNCENSORED-HAUHAUCS-AGGRESSIVE-BF16-Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-THINKINGCAP-QWEN3.6-27B-F16-ThinkingCap-Qwen3.6-27B.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mmproj-qwen2.5-omni-7b-f16.gguf` | None | ⚠ none / gated | _no candidates_ |
| `mxbai-embed-large-v1.Q4_K_M.gguf` | None | ⚠ throttled — verify | ChristianAzinn/mxbai-embed-large-v1-gguf |
| `nex-agi_Nex-N2-mini-Q6_K_L.gguf` | None | ⚠ throttled — verify | adamm-hf/nex-agi_Nex-N2-mini-GGUF |
| `ornith-1.0-35b-Q6_K.gguf` | None | ⚠ throttled — verify | ornith-ai/Ornith-1.0-35B-GGUF |
| `qwen2.5-omni-7b-q4_k_m.gguf` | None | ⚠ throttled — verify | GaryGao99/Qwen2.5-Omni-7B-Q4_K_M-GGUF |