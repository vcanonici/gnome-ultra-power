# Recuperação e diagnóstico

Comece com uma saída normal pela sessão do usuário:

```bash
ultra-power
ultra-power off
journalctl -u ultra-power -b --no-pager -n 80
```

Se o serviço não responder, use um terminal ou TTY local. O serviço tem recuperação no `ExecStopPost`:

```bash
sudo systemctl stop ultra-power.service
sudo systemctl start ultra-power.service
ultra-power
```

O estado esperado é `Active: false`, `Pending: false`, `Applying: false`. Confira a afinidade da sua conta com `systemctl show user-$(id -u).slice -p AllowedCPUs` e o perfil com `powerprofilesctl get`.

Um journal em `/var/lib/ultra-power/recovery.json` significa que ainda há algo a restaurar. **Não apague esse arquivo para liberar a ativação.** Se o serviço permanecer parado, tente explicitamente:

```bash
sudo /usr/bin/python3 /usr/local/lib/ultra-power/daemon.py restore
```

A recuperação usa o journal privado, valida os caminhos e retém o arquivo se algum passo falhar. Não edite UID, listas de CPUs, permissões ou caminhos por tentativa. Abra um issue com o erro relevante, revisando dados pessoais antes de compartilhar logs. Não publique o journal privado.

## Perguntas comuns

- **ULTRA não aparece:** confirme GNOME 46 e sessão Wayland, saia/entre, consulte `gnome-extensions info ultra-power@vcanonici` e `systemctl status ultra-power`.
- **Digital falha:** confirme `fprintd-list "$USER"` e verifique se outro programa está usando o leitor. A v1 não substitui falha de biometria por senha.
- **GPU ativa:** ULTRA já está funcionando com economia parcial. Verifique cargas CUDA, programas em offload e saídas externas; o modo nunca mata o compositor.
- **Sem leitor:** a v1 exige fprintd e digital cadastrada.
- **Preciso manter acesso remoto:** ULTRA pausa o GNOME Remote Desktop do usuário; escolha um perfil normal para retomá-lo. Acesso remoto não pode aprovar a ativação.
- **Já uso TLP/tuned:** o instalador recusa dois coordenadores ativos. Não desative ferramentas existentes sem decidir qual política quer manter.
- **A sessão ficou lenta:** use “mais fluidez” ou saia do ULTRA. As restrições são intencionais e abrangem todas as aplicações da conta.

O produto não altera GDM, kernel, initramfs, drivers, firmware, firewall ou PAM de login/sudo. Se o login gráfico já estava falhando antes da instalação, investigue esse incidente separadamente.
