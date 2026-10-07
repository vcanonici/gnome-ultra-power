# Histórico

## 1.1.0 — 2026-10-07

- Confirmação alternativa pelo botão vermelho OK, equivalente à escolha da digital.
- Explicação do objetivo: evitar falhas de operação por ativação acidental.
- Digital começa somente ao escolher Usar digital; falha permite tentar novamente ou usar OK.
- Mesmas verificações de UID, sessão, bateria, processos protegidos e journal para ambos os caminhos.
- Instalação permitida sem digital cadastrada; leitores continuam opcionais para aprovação biométrica.
- Evita autoativação D-Bus e espera por Bluetooth ausente.
- 52 testes e mypy strict; teste gráfico do botão e de troca de aprovação na VM.

## 1.0.0 — 2026-10-07

- Primeira distribuição pública independente do modo ULTRA.
- Integração no menu GNOME 46, aprovação por digital e ícone amarelo.
- Descoberta de topologia/UID/GPU; CPUs continuam online por padrão.
- Hotplug e pausa de containers/VMs disponíveis somente por opção explícita.
- VPNs preservadas; economia parcial visível quando NVIDIA permanece ativa.
- Instalador com diagnóstico, rejeição de conflitos, manifesto e desinstalação.
- Recuperação transacional e tratamento de políticas cpufreq inativas.
- Testes, mypy estrito, CI e pacotes reproduzíveis com SHA256.

Suporte inicial: Ubuntu 24.04, GNOME 46, Wayland, x86_64, bateria e fprintd.
