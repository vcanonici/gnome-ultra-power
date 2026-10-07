# Validação da v1.1.0

Data: 2026-10-07. Esta lista distingue testes do pacote público e a referência física do controlador original.

## Pacote público

- **51 testes**: configuração estrita, descoberta de topologia híbrida/SMT/um núcleo e IDs diferentes, recuperação, proteção de processos, revisão de PID/pidfd, cpufreq inativa/EBUSY e instalador.
- **mypy strict** aprovado em nove arquivos Python, com stubs para D-Bus.
- Sintaxe ESModule da extensão, metadata JSON e unidade systemd verificadas.
- Regressão de permissão: biblioteca legível pela sessão (0755) e módulos 0644 mesmo com umask 077; manifesto privado 0700.
- O padrão não escreve hotplug nem durante a recuperação; a opção explícita continua testada.

## VM Ubuntu 24.04 / GNOME 46 / Wayland

Conta gráfica UID **1001**, diferente do UID1000 habitual; quatro CPUs virtuais; sem NVIDIA; bateria e leitor simulados com os serviços oficiais de teste fprintd/python-dbusmock, **exclusivos da VM**.

| Cenário | Resultado |
| --- | --- |
| Diagnóstico e instalação completos | Aprovados; serviço e extensão carregados |
| Cancelar / digital rejeitada | Nenhum ajuste aplicado |
| Digital aprovada, padrão | Afinidade 1–2; quatro CPUs continuam online |
| Mais fluidez / saída | Afinidade ampliada e baseline restaurado |
| Nova tentativa pela digital | Nova leitura exigida |
| Hotplug opt-in | Online 0–2, afinidade 1–2; fluidez/saída restauram 0–3 |
| Menu nativo | ULTRA visível, check e bateria amarela; “Sem GPU NVIDIA dedicada” |
| SIGKILL do serviço ativo | systemd executou recuperação/reinício; journal eliminado, afinidade restaurada, ULTRA inativo |
| Conectar carregador | Sai do modo, recupera ajustes, fallback equilibrado quando não existe perfil desempenho |
| Desconectar carregador | Economia de energia; não ativa ULTRA automaticamente |
| Desinstalar | Remove arquivos ULTRA, conserva outras entradas de extensões e confere hashes PAM |
| Falha de cópia na instalação | Rollback remove arquivos parciais e extensão; arquiva manifesto |

As capturas em `docs/assets/` são da VM. O teste de falha revelou uma colisão no nome de arquivo de backup ao remover e instalar no mesmo processo; corrigida com nome único e cenário repetido com sucesso. A instalação inicial também revelou a permissão restrita da biblioteca; corrigida e coberta por teste.

## Referência física anterior

ThinkPad T15p: o operador confirmou ativação com digital real e ícone amarelo. O controlador original verificou CPU reduzida, EPP power, turbo desligado e NVIDIA runtime suspended/VRAM Off, mantendo o compositor Intel. A correção de EPP antes do hotplug foi validada fisicamente.

**A instalação pública não substituiu esse controlador no ThinkPad durante a publicação.** Não afirmar que todas as variantes Intel/AMD, leitores, saídas externas ou firmwares foram testados. A VM não mede autonomia nem reproduz a suspensão elétrica de uma GPU NVIDIA.

## Limites

Sem benchmark comparativo de horas de bateria ou consumo em watts na distribuição pública. Sem suporte declarado a GNOME diferente de 46, outras distribuições ou Xorg. Bluetooth, brilho, EPP e teclado variam com hardware. CI verifica código/empacotamento; testes físicos continuam necessários para ampliar a matriz.

## Confirmação alternativa da v1.1.0

O OK vermelho confirma conscientemente os compromissos, como alternativa à digital. A finalidade é evitar falhas de operação por ativação acidental, enquanto as verificações técnicas e o rollback permanecem no serviço. O OK não é um segundo fator; consulte SECURITY.md.

Oito testes adicionais verificam confirmação sem PAM, recusa com AC/sessão bloqueada/root/outro UID, proteção de processos, revalidação de identidade, troca da própria aprovação pendente e recusa de troca da aprovação de outro cliente ou recuperação pendente.

Na VM, o diagnóstico aceitou ausência de cadastro, a falha da digital conservou o OK vermelho, e o clique ativou ULTRA sem biometria. A troca de uma leitura pendente para OK cancelou o worker PAM e ativou em menos de um segundo; saída restaurou o baseline. O teste por digital da v1.0 foi repetido na v1.1 com sucesso. Bluetooth sem owner D-Bus não é autoativado; isso removeu uma espera de 25 segundos da fixture.
