# Segurança

A v1.0.x recebe correções no ambiente suportado. Para vulnerabilidades, use [Report a vulnerability](https://github.com/vcanonici/gnome-ultra-power/security/advisories/new), de forma privada. Para falhas operacionais sem implicação de segurança, abra um issue.

O controlador é um serviço root que altera energia, cgroups e serviços locais. A ativação exige a conta configurada, sessão Wayland local desbloqueada, bateria e uma aprovação nova via fprintd/PAM. Biometria tem os limites do leitor e da sua configuração fprintd; este projeto não implementa algoritmos biométricos nem gerencia seus templates.

Só o PAM `ultra-power` é instalado. Não se altera o PAM de login gráfico/sudo, nem se salva senha ou digital no projeto. A recuperação é root-only, validada e preservada quando incompleta. O encerramento forçado de aplicações é opcional, requer revisão explícita e nunca deve atingir o compositor ou processos de outros usuários.

Checksums verificam integridade do download. Eles não substituem revisão do código nem representam uma assinatura independente do autor. Baixe da release oficial deste repositório.
