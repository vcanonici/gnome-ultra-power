# Contribuir

Comece por um issue descrevendo Ubuntu, GNOME, Wayland, CPU/GPU e leitor. Suporte a outra versão do Shell exige teste real do menu, aprovação/cancelamento e descarregamento da extensão.

Execute `scripts/check.sh`. Mudanças nos controles de hardware exigem snapshots e rollback testados; mudanças em autenticação exigem teste de rejeição/cancelamento, além do caso aprovado. Mantenha tanto a digital quanto o OK vermelho sujeitos às mesmas verificações de UID, sessão, bateria, processos e recuperação. Não adicione ativação automática nem IDs particulares de uma máquina ao produto.

Use VM descartável para PAM/D-Bus simulados. Nunca instale mocks de autenticação num computador de uso diário. Antes de sugerir suporte a um novo dispositivo, teste o pacote completo e a remoção nesse dispositivo. Não inclua credenciais, journals privados, imagens de disco ou configurações pessoais em PRs.

Para reproduzir os artefatos: `python3 scripts/build-release.py`. O script empacota somente arquivos rastreados pelo Git; saída em `artifacts/`. Atualize VERSION, CHANGELOG e evidências antes de publicar uma versão.
