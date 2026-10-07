# Segurança

A v1.1.x recebe correções no ambiente suportado. Para vulnerabilidades, use [Report a vulnerability](https://github.com/vcanonici/gnome-ultra-power/security/advisories/new), de forma privada. Para falhas operacionais sem implicação de segurança, abra um issue.

O controlador é um serviço root que altera energia, cgroups e serviços locais. A ativação exige a conta configurada, sessão Wayland local desbloqueada, bateria e confirmação explícita pela digital ou OK vermelho. O objetivo dessa confirmação é evitar falhas de operação por ativação acidental.

O botão OK não é um segundo fator de autenticação. A autoridade para ativar é a conta local configurada na sessão desbloqueada; um programa dessa mesma conta também pode chamar a API ConfirmEnable. A interface não consegue comprovar um clique físico ao serviço root. Essa alternativa foi adicionada deliberadamente: mantenha a conta e a sessão protegidas pelo login do sistema. Biometria tem os limites do leitor e da sua configuração fprintd; este projeto não implementa algoritmos biométricos nem gerencia seus templates.

Só o PAM `ultra-power` é instalado. Não se altera o PAM de login gráfico/sudo, nem se salva senha ou digital no projeto. A recuperação é root-only, validada e preservada quando incompleta. O encerramento forçado de aplicações é opcional, requer revisão explícita e nunca deve atingir o compositor ou processos de outros usuários.

Checksums verificam integridade do download. Eles não substituem revisão do código nem representam uma assinatura independente do autor. Baixe da release oficial deste repositório.
