Name:           yaesm
Version:        0.0.1
Release:        1%{?dist}
Summary:        Backup tool with support for multiple filesystems

License:        GPL-3.0-or-later
URL:            https://github.com/Vultimate1/yaesm
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz#/%{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros

Requires:       btrfs-progs
Requires:       openssh-clients
Requires:       rsync

%description
Yaesm creates and schedules backups using Btrfs snapshots or file
synchronization. It supports local and remote backups, configurable retention
periods, and searching for backups by name and time.


%generate_buildrequires
%pyproject_buildrequires


%prep
%autosetup -p1
sed -i '1{/^#!\/usr\/bin\/env python3$/d}' src/yaesm/main.py


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files yaesm

install -Dpm 0644 packaging/systemd/yaesm.service \
    %{buildroot}%{_unitdir}/yaesm.service
install -Dpm 0644 man/yaesm.1 \
    %{buildroot}%{_mandir}/man1/yaesm.1
install -dm 0755 %{buildroot}%{_sysconfdir}/yaesm


%check
# The full test suite must run in the isolated VM provided by upstream.
%pyproject_check_import


%post
%systemd_post yaesm.service

%preun
%systemd_preun yaesm.service

%postun
%systemd_postun_with_restart yaesm.service


%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/yaesm
%{_unitdir}/yaesm.service
%{_mandir}/man1/yaesm.1*
%dir %{_sysconfdir}/yaesm


%changelog
* Fri Aug 21 2026 Nicholas B. Hubbard <nicholashubbard@posteo.net> - 0.0.1-1
- Initial package
