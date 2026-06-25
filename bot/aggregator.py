from requests import post


def get_pdf_urls():
    all_reports_list = []
    reports_in_last_request = -1
    page_num = 1

    while reports_in_last_request != 0:
        reports = post(
            "https://www.knesset.gov.il/WebSiteApi/knessetapi/CommitteeReports/GetCommitteePortalsBudget",
            json={
                "FromDate": None,
                "ToDate": None,
                "requestStatus": 2,
                "PageNumber": page_num,
            },
        ).json()

        all_reports_list += reports
        reports_in_last_request = len(reports)
        page_num += 1

    return [request["ReportUrl"] for request in all_reports_list]
