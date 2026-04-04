%global srcname yaesm

Name:		python3-%{srcname}
Version:	0.0.1
Release:	1%{dist}
Summary:	Yet another backup scheduler.

License:	GPL-3.0-or-later
URL:		https://github.com/Vultimate1/%{srcname}
Source0:	%{url}/archive/refs/tags/v%{version}.tar.gz

BuildArch:	noarch
BuildRequires:	python3-devel
BuildRequires:	python3dist(pytest)

%global _description %{expand:
yaesm is a backup scheduler for multiple filesystems.
}

%description %_description

%prep
%autosetup -p1 -n %{srcname}-%{version}


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l %{srcname}

%check
%pyproject_check_import %{srcname}

%files -n %{name} -f %{pyproject_files}
%doc README.md
%license LICENSE
%{_bindir}/%{srcname}

%changelog
%autochangelog
