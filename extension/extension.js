// SPDX-License-Identifier: GPL-3.0-or-later
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const NAME = 'io.github.vcanonici.UltraPower';
const XML = `<node><interface name="${NAME}">
<method name="GetBlockers"><arg type="a(ssb)" direction="out"/></method>
<method name="RequestEnable"><arg type="b" direction="in"/><arg type="as" direction="in"/></method>
<method name="ConfirmEnable"><arg type="b" direction="in"/><arg type="as" direction="in"/></method>
<method name="Cancel"/><method name="Disable"><arg type="s" direction="in"/></method>
<method name="SetPreset"><arg type="s" direction="in"/></method>
<property name="Active" type="b" access="read"/><property name="Pending" type="b" access="read"/>
<property name="Applying" type="b" access="read"/><property name="CpuPreset" type="s" access="read"/>
<property name="LastError" type="s" access="read"/><property name="ApprovalMessage" type="s" access="read"/>
<property name="GpuRuntimeState" type="s" access="read"/><property name="GpuWarning" type="s" access="read"/>
</interface></node>`;
const Proxy = Gio.DBusProxy.makeProxyWrapper(XML);

export default class Ultra extends Extension {
    enable() {
        this._enabled = true;
        this._signals = [];
        this._stockSignals = [];
        this._proxySignals = [];
        this._lastError = '';
        if (Main.layoutManager._startingUp) {
            this._startupSignal = Main.layoutManager.connect('startup-complete', () => {
                Main.layoutManager.disconnect(this._startupSignal);
                this._startupSignal = null;
                if (this._enabled) this._attach();
            });
        } else this._attach();
    }
    _attach() {
        this._toggle = Main.panel.statusArea.quickSettings?._powerProfiles?.quickSettingsItems[0];
        this._battery = Main.panel.statusArea.quickSettings?._system?._indicator;
        if (!this._toggle || !this._battery)
            throw new Error('GNOME ULTRA Power exige o menu de energia do GNOME 46.');
        this._attached = true;
        this._row = new PopupMenu.PopupImageMenuItem('ULTRA', 'power-profile-power-saver-symbolic');
        this._toggle.menu.addMenuItem(this._row, 1);
        this._responsive = new PopupMenu.PopupMenuItem('ULTRA: mais fluidez');
        this._toggle.menu.addMenuItem(this._responsive, 2);
        this._gpuStatus = new PopupMenu.PopupMenuItem('', {reactive: false, can_focus: false});
        this._gpuStatus.label.clutter_text.line_wrap = true;
        this._toggle.menu.addMenuItem(this._gpuStatus, 3);
        this._signals.push([this._toggle.menu, this._toggle.menu.connect('open-state-changed', (_menu, open) => {
            if (open) this._refreshGpu();
        })]);
        this._signals.push([this._row, this._row.connect('activate', () => this._activate())]);
        this._signals.push([this._responsive, this._responsive.connect('activate', () => {
            const preset = this._proxy?.CpuPreset === 'responsive' ? 'minimal' : 'responsive';
            this._call('SetPreset', [preset]);
        })]);
        this._originalSync = this._toggle._sync;
        this._originalProfiles = this._toggle._syncProfiles;
        this._toggle._sync = (...args) => { this._originalSync.apply(this._toggle, args); this._paint(); };
        this._toggle._syncProfiles = (...args) => {
            this._disconnect(this._stockSignals);
            this._originalProfiles.apply(this._toggle, args);
            this._hookProfiles();
        };
        this._hookProfiles();
        this._signals.push([Main.sessionMode, Main.sessionMode.connect('updated', () => {
            if (Main.sessionMode.isLocked && this._proxy?.Pending) {
                this._call('Cancel');
                this._closeDialog();
            }
        })]);
        this._proxy = new Proxy(Gio.DBus.system, NAME, '/io/github/vcanonici/UltraPower', (proxy, error) => {
            if (!this._enabled) return;
            if (error) { console.error(error); this._paint(); return; }
            this._proxy = proxy;
            this._proxySignals.push([proxy, proxy.connect('g-properties-changed', () => this._changed())]);
            this._proxySignals.push([proxy, proxy.connect('notify::g-name-owner', () => this._changed())]);
            this._changed();
        });
        this._paint();
    }

    _disconnect(list) {
        for (const [object, id] of list.splice(0)) {
            try { object.disconnect(id); } catch (_) { /* destroyed native row */ }
        }
    }
    _hookProfiles() {
        for (const [profile, item] of this._toggle._profileItems) {
            this._stockSignals.push([item, item.connect('activate', () => {
                if (this._proxy?.Active || this._proxy?.Pending) this._call('Disable', [profile]);
            })]);
        }
    }
    _call(method, args = [], callback = null) {
        if (!this._proxy?.g_name_owner) return;
        this._proxy[`${method}Remote`](...args, (result, error) => {
            if (!this._enabled) return;
            if (error) { this._closeDialog(); this._showError(error.message); }
            else callback?.(result);
        });
    }
    _refreshGpu() {
        if (!this._proxy?.g_name_owner) return;
        Gio.DBus.system.call(NAME, '/io/github/vcanonici/UltraPower',
            'org.freedesktop.DBus.Properties', 'GetAll',
            new GLib.Variant('(s)', [NAME]), null, Gio.DBusCallFlags.NONE, 5000, null,
            (connection, result) => {
                if (!this._enabled || !this._proxy?.g_name_owner) return;
                try {
                    const [values] = connection.call_finish(result).deep_unpack();
                    for (const key of ['GpuRuntimeState', 'GpuWarning']) {
                        if (values[key]) this._proxy.set_cached_property(key, values[key]);
                    }
                    this._paint();
                } catch (error) { console.error(error); }
            });
    }
    _paint() {
        const available = Boolean(this._proxy?.g_name_owner);
        const active = available && Boolean(this._proxy.Active);
        const gpuSuspended = ['suspended', 'not-present'].includes(this._proxy?.GpuRuntimeState);
        this._row.visible = available;
        if (available) this._toggle.menuEnabled = true;
        this._row.setSensitive(available && !this._proxy.Applying);
        this._row.setOrnament(active ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
        this._responsive.visible = active;
        this._gpuStatus.visible = active;
        this._gpuStatus.label.text = this._proxy?.GpuWarning || (gpuSuspended
            ? (this._proxy?.GpuRuntimeState === 'not-present' ? 'Sem GPU NVIDIA dedicada' : 'NVIDIA suspensa — economia da GPU confirmada') : 'Estado da NVIDIA não confirmado');
        this._responsive.setSensitive(active && !this._proxy.Applying);
        this._responsive.label.text = this._proxy?.CpuPreset === 'responsive'
            ? 'ULTRA: voltar à economia máxima' : 'ULTRA: mais fluidez';
        if (active) {
            this._battery.add_style_class_name('ultra-active');
            for (const item of this._toggle._profileItems.values()) item.setOrnament(PopupMenu.Ornament.NONE);
            this._toggle.subtitle = gpuSuspended ? 'ULTRA' : 'ULTRA · economia parcial';
            this._toggle.iconName = 'power-profile-power-saver-symbolic';
            this._toggle.checked = true;
        } else this._battery.remove_style_class_name('ultra-active');
    }
    _changed() {
        this._toggle._sync();
        if (!this._proxy.g_name_owner) { this._closeDialog(); return; }
        if (this._proxy.Applying && this._dialog) {
            this._body.text = 'Aplicando os ajustes. Aguarde…';
            this._approval?.digital?.set_reactive(false);
            this._approval?.ok?.set_reactive(false);
        }
        else if (this._proxy.Pending && this._dialog)
            this._body.text = 'CPU reduzida, tela até 20% quando disponível, retroiluminação no nível mínimo quando disponível. Acesso remoto e indexação serão pausados; VPNs serão preservadas.\n\n' + 'A confirmação evita ativação acidental. Use o dedo cadastrado ou clique no OK vermelho.';
        else if (this._proxy.Active) {
            const completed = Boolean(this._dialog);
            this._closeDialog();
            if (completed && this._proxy.GpuWarning)
                Main.notify('ULTRA: economia parcial', this._proxy.GpuWarning);
        }
        else if (this._proxy.LastError && this._proxy.LastError !== this._lastError) {
            this._lastError = this._proxy.LastError;
            if (this._approval && this._dialog) {
                this._body.text = this._lastError + '\n\nVocê pode tentar a digital novamente ou confirmar no OK vermelho.';
                this._approval.digital.set_reactive(true);
                this._approval.ok.set_reactive(true);
            } else { this._closeDialog(); this._showError(this._lastError); }
        }
    }
    _dialogBox(title, body) {
        this._closeDialog();
        this._dialog = new ModalDialog.ModalDialog();
        const currentDialog = this._dialog;
        currentDialog.connect('destroy', () => {
            if (this._dialog === currentDialog) { this._dialog = null; this._body = null; }
        });
        this._dialog.contentLayout.add_child(new St.Icon({icon_name: 'fingerprint-detection-complete-symbolic', style_class: 'ultra-dialog-icon'}));
        this._dialog.contentLayout.add_child(new St.Label({text: title, style_class: 'ultra-dialog-title'}));
        this._body = new St.Label({text: body, style_class: 'ultra-dialog-body'});
        this._body.clutter_text.line_wrap = true;
        this._dialog.contentLayout.add_child(this._body);
        return this._dialog;
    }
    _closeDialog() {
        this._approval = null;
        if (this._dialog) { const dialog = this._dialog; this._dialog = null; this._body = null; dialog.close(); }
    }
    _showError(message) {
        const dialog = this._dialogBox('ULTRA não foi ativado', message);
        dialog.setButtons([{label: 'Fechar', action: () => this._closeDialog(), key: Clutter.KEY_Escape}]);
        dialog.open();
    }
    _activate() {
        if (this._proxy.Active) { this._call('Disable', ['']); return; }
        if (this._proxy.Pending || this._proxy.Applying) return;
        Main.panel.statusArea.quickSettings.menu.close();
        this._call('GetBlockers', [], result => {
            const rows = result[0];
            if (rows.length) {
                const names = rows.map(row => `${row[1]} (PID ${row[0].split(':')[0]})`).join('\n');
                const killable = rows.every(row => row[2]);
                const dialog = this._dialogBox('Processos com acesso à NVIDIA',
                    `${names}\n\nVocê pode continuar com economia parcial e manter estes processos. A NVIDIA poderá continuar ligada. ${killable ? 'Encerrar à força pode perder trabalho não salvo.' : 'Processos protegidos serão preservados.'} A ativação exige confirmação pela digital ou OK vermelho.`);
                const buttons = [{label: 'Cancelar', action: () => this._closeDialog(), key: Clutter.KEY_Escape}];
                buttons.push({label: 'Continuar com economia parcial', action: () => this._approve(false, [])});
                if (killable) buttons.push({label: 'Encerrar à força e continuar', action: () => this._approve(true, rows.map(row => row[0]))});
                dialog.setButtons(buttons); dialog.open();
            } else this._approve(false, []);
        });
    }
    _approve(force, reviewed) {
        this._lastError = '';
        const dialog = this._dialogBox('Confirmar ULTRA',
            'CPU reduzida, brilho até 20% e retroiluminação mínima quando disponíveis. Acesso remoto e indexação serão pausados; VPNs serão preservadas.\n\nA digital ajuda a evitar falhas de operação por ativação acidental. Você também pode confirmar clicando no OK vermelho. As verificações do sistema e a recuperação continuam automáticas.');
        dialog.setButtons([{label: 'Cancelar', action: () => {
            this._call('Cancel'); this._closeDialog();
        }, key: Clutter.KEY_Escape}]);
        const digital = dialog.addButton({label: 'Usar digital', action: () => {
            digital.set_reactive(false);
            this._lastError = '';
            this._body.text = 'Coloque o dedo cadastrado no leitor. Para evitar ativação acidental, confirme com a digital ou clique no OK vermelho.';
            this._call('RequestEnable', [force, reviewed]);
        }});
        const ok = dialog.addButton({label: 'OK', action: () => {
            digital.set_reactive(false); ok.set_reactive(false);
            this._call('ConfirmEnable', [force, reviewed]);
        }});
        ok.add_style_class_name('ultra-approve-ok');
        this._approval = {digital, ok};
        dialog.open();
    }
    disable() {
        this._call('Cancel');
        this._call('Disable', ['']);
        this._enabled = false;
        if (this._startupSignal) Main.layoutManager.disconnect(this._startupSignal);
        this._startupSignal = null;
        this._closeDialog();
        this._disconnect(this._signals);
        this._disconnect(this._stockSignals);
        this._disconnect(this._proxySignals);
        this._battery?.remove_style_class_name('ultra-active');
        this._row?.destroy(); this._responsive?.destroy(); this._gpuStatus?.destroy();
        if (this._attached && this._toggle) {
            this._toggle._sync = this._originalSync;
            this._toggle._syncProfiles = this._originalProfiles;
            this._toggle._sync();
        }
        this._proxy = null; this._attached = false;
    }
}
